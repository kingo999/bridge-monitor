// Windows ETW metadata reader. Only the fields emitted below cross the pipe.
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <tlhelp32.h>
#include <evntrace.h>
#include <evntcons.h>
#include <tdh.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <stdexcept>
#include <thread>
#include <atomic>
#include <mutex>
#include <unordered_map>
#pragma comment(lib,"advapi32.lib")
#pragma comment(lib,"tdh.lib")
#pragma comment(lib,"ws2_32.lib")

static const GUID network = {0x7dd42a49,0x5329,0x4832,{0x8d,0xfd,0x43,0xd9,0x79,0x15,0x3a,0x88}};
static const GUID processes = {0x22fb2cd6,0x0e7b,0x422b,{0xa0,0xc7,0x2f,0xad,0x1f,0xd0,0xe7,0x16}};
static TRACEHANDLE session;
static ULONG onlyPid; // Optional restricted smoke test. Never emit unrelated process or flow records.
static bool processProviderMissing;
static ULONGLONG currentBirth(ULONG pid) {
    HANDLE process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);
    if (!process) return 0;
    FILETIME start{},end{},kernel{},user{};
    ULARGE_INTEGER birth{};
    if (GetProcessTimes(process,&start,&end,&kernel,&user)) {
        birth.LowPart=start.dwLowDateTime; birth.HighPart=start.dwHighDateTime;
    }
    CloseHandle(process);
    return birth.QuadPart;
}
struct Identity { ULONG pid; ULONGLONG born; ULONG parent; std::string image; };
struct ProcessEvidence { Identity self; std::vector<Identity> ancestors; };
static std::unordered_map<ULONG, ProcessEvidence> knownProcesses;
static std::mutex evidenceMutex;
static void emitIdentity(const Identity& v) {
    printf("start\t%lu\t%llu\t%lu\t%s\n",v.pid,v.born,v.parent,v.image.c_str());
}
static void remember(const Identity& v) {
    ProcessEvidence evidence{v,{}};
    auto parent=knownProcesses.find(v.parent);
    if (parent!=knownProcesses.end() && parent->second.self.born<v.born) {
        evidence.ancestors=parent->second.ancestors;
        evidence.ancestors.push_back(parent->second.self);
    }
    knownProcesses[v.pid]=evidence;
}
static void emitKnown(ULONG pid, ULONGLONG at) {
    auto evidence=knownProcesses.at(pid);
    for (const auto& ancestor:evidence.ancestors) {
        auto current=knownProcesses.find(ancestor.pid);
        auto live=currentBirth(ancestor.pid);
        if (current!=knownProcesses.end() && current->second.self.born==ancestor.born &&
                (!live || live==ancestor.born || live>at))
            emitIdentity(ancestor);
        else printf("end\t%lu\t%llu\t%llu\n",ancestor.pid,ancestor.born,at);
    }
    emitIdentity(evidence.self);
}
static const GUID winsock = {0xe53c6823,0x7bb8,0x44bb,{0x90,0xdc,0x3f,0x86,0x09,0x0d,0x48,0xa6}};
static std::wstring name;
struct Properties { EVENT_TRACE_PROPERTIES p; wchar_t text[128]; };
static Properties props() {
    Properties v{};
    v.p.Wnode.BufferSize = sizeof(v);
    v.p.Wnode.Flags = WNODE_FLAG_TRACED_GUID;
    v.p.Wnode.ClientContext = 1;
    v.p.LogFileMode = EVENT_TRACE_REAL_TIME_MODE;
    v.p.FlushTimer = 1;
    v.p.LoggerNameOffset = offsetof(Properties,text);
    return v;
}
static BOOL WINAPI stop(DWORD signal) {
    if (signal != CTRL_C_EVENT && signal != CTRL_BREAK_EVENT && signal != CTRL_CLOSE_EVENT) return FALSE;
    auto v = props();
    ControlTraceW(session,name.c_str(),&v.p,EVENT_TRACE_CONTROL_STOP);
    return TRUE;
}
static std::vector<BYTE> field(EVENT_RECORD* r, const wchar_t* key) {
    PROPERTY_DATA_DESCRIPTOR d{};
    d.PropertyName = reinterpret_cast<ULONGLONG>(key);
    d.ArrayIndex = ULONG_MAX;
    ULONG n = 0;
    if (TdhGetPropertySize(r,0,nullptr,1,&d,&n) || !n || n > 65536)
        throw std::runtime_error("field size");
    std::vector<BYTE> b(n);
    if (TdhGetProperty(r,0,nullptr,1,&d,n,b.data())) throw std::runtime_error("field read");
    return b;
}
template<typename T> static T number(EVENT_RECORD* r, const wchar_t* key) {
    auto b = field(r,key);
    if (b.size()!=sizeof(T)) throw std::runtime_error("field type");
    T v; memcpy(&v,b.data(),sizeof(v)); return v;
}
// A restricted account may see socket events without process-start events. Reconstruct
// the still-live ancestry only when each PID's creation time precedes its child/event.
static void liveProcess(ULONG pid, ULONGLONG latest, unsigned depth=0) {
    if (!pid || depth>=64) return;
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);
    FILETIME start{},end{},kernel{},user{};
    ULARGE_INTEGER birth{};
    if (process && GetProcessTimes(process,&start,&end,&kernel,&user)) {
        birth.LowPart=start.dwLowDateTime; birth.HighPart=start.dwHighDateTime;
    }
    if (!birth.QuadPart || birth.QuadPart>latest) {
        if (process) CloseHandle(process);
        printf("unknown\t%lu\t%llu\n",pid,latest);
        return;
    }
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    PROCESSENTRY32W ent{}; ent.dwSize=sizeof(ent);
    bool found = false;
    if (snapshot!=INVALID_HANDLE_VALUE) {
        if (Process32FirstW(snapshot,&ent)) do {
            if (ent.th32ProcessID==pid) { found=true; break; }
        } while (Process32NextW(snapshot,&ent));
        CloseHandle(snapshot);
    }
    if (found) {
        if (!onlyPid && ent.th32ParentProcessID!=pid)
            liveProcess(ent.th32ParentProcessID,birth.QuadPart-1,depth+1);
        char image[4*MAX_PATH]{};
        for (auto &c:ent.szExeFile) if (c && (c<32 || c==127)) c=L'?';
        WideCharToMultiByte(CP_UTF8,0,ent.szExeFile,-1,image,sizeof(image),nullptr,nullptr);
        Identity identity{pid,birth.QuadPart,ent.th32ParentProcessID,image};
        emitIdentity(identity);
        remember(identity);
    } else printf("unknown\t%lu\t%llu\n",pid,latest);
    CloseHandle(process);
}
static void WINAPI consume(EVENT_RECORD* r) {
    // Keep complete identity/flow groups ordered relative to a loss marker.
    std::lock_guard<std::mutex> lock(evidenceMutex);
    const auto &h = r->EventHeader;
    auto id = h.EventDescriptor.Id;
    auto t = h.TimeStamp.QuadPart;
    try {
        if (IsEqualGUID(h.ProviderId,processes) && (id==1 || id==2)) {
            auto pid = number<ULONG>(r,L"ProcessID");
            if (onlyPid && pid!=onlyPid) return;
            auto born = number<ULONGLONG>(r,L"CreateTime");
            if (id==2) {
                auto old = knownProcesses.find(pid);
                if (old!=knownProcesses.end() && old->second.self.born==born) knownProcesses.erase(old);
                printf("end\t%lu\t%llu\t%lld\n",pid,born,t); return;
            }
            auto parent = number<ULONG>(r,L"ParentProcessID");
            if (!onlyPid && parent!=pid && knownProcesses.find(parent)==knownProcesses.end())
                liveProcess(parent,born-1);
            auto bytes = field(r,L"ImageName");
            std::wstring image(reinterpret_cast<wchar_t*>(bytes.data()),bytes.size()/2);
            image = image.substr(0,image.find(L'\0'));
            auto pos = image.find_last_of(L"\\/");
            if (pos != std::wstring::npos) image.erase(0,pos+1);
            for (auto &c : image) if (c < 32 || c==127) c=L'?';
            std::string utf8(image.size()*4+1,'\0');
            WideCharToMultiByte(CP_UTF8,0,image.c_str(),-1,&utf8[0],int(utf8.size()),nullptr,nullptr);
            Identity identity{pid,born,parent,utf8.c_str()};
            emitIdentity(identity);
            remember(identity);
        } else if (IsEqualGUID(h.ProviderId,network) || IsEqualGUID(h.ProviderId,winsock)) {
            const bool attempt = IsEqualGUID(h.ProviderId,winsock);
            const bool sockaddrEvent = id==1018 || id==1021;
            if (attempt ? (id!=4 && id!=5 && !sockaddrEvent) : (id!=10 && id!=26 && id!=12 && id!=28 && id!=42 && id!=58)) return;
            auto pid = attempt ? h.ProcessId : number<ULONG>(r,L"PID");
            if (onlyPid && pid!=onlyPid) return;
            auto cached=knownProcesses.find(pid);
            if (cached!=knownProcesses.end()) {
                auto live=currentBirth(pid);
                if (live && live<=static_cast<ULONGLONG>(t) && live!=cached->second.self.born)
                    knownProcesses.erase(cached);
            }
            if (processProviderMissing || knownProcesses.find(pid)==knownProcesses.end())
                liveProcess(pid,t);
            else emitKnown(pid,t);
            auto ip = field(r,attempt ? L"Address" : L"daddr");
            char dest[INET6_ADDRSTRLEN]{};
            if (sockaddrEvent) {
                if (ip.size()<sizeof(sockaddr_in)) return;
                auto sa = reinterpret_cast<const sockaddr*>(ip.data());
                USHORT port;
                const void* addr;
                if (sa->sa_family==AF_INET) {
                    auto v4 = reinterpret_cast<const sockaddr_in*>(sa);
                    port = ntohs(v4->sin_port); addr = &v4->sin_addr; id=4;
                } else if (sa->sa_family==AF_INET6 && ip.size()>=sizeof(sockaddr_in6)) {
                    auto v6 = reinterpret_cast<const sockaddr_in6*>(sa);
                    port = ntohs(v6->sin6_port); addr = &v6->sin6_addr; id=5;
                } else return;
                if (!InetNtopA(sa->sa_family,addr,dest,sizeof(dest))) throw std::runtime_error("sockaddr");
                printf("net\t%lld\t%lu\t%u\t%s\t%u\t0\n",t,pid,id,dest,port);
                return;
            }
            if ((ip.size()!=4 && ip.size()!=16) || !InetNtopA(ip.size()==4 ? AF_INET : AF_INET6,
                    ip.data(),dest,sizeof(dest))) throw std::runtime_error("address");
            auto port = ntohs(number<USHORT>(r,attempt ? L"Port" : L"dport"));
            auto size = attempt ? 0UL : number<ULONG>(r,L"size");
            if (attempt) printf("net\t%lld\t%lu\t%u\t%s\t%u\t0\n",t,pid,id,dest,port);
            else {
                auto src = field(r,L"saddr");
                char local[INET6_ADDRSTRLEN]{};
                if (src.size()!=ip.size() || !InetNtopA(src.size()==4 ? AF_INET : AF_INET6,
                        src.data(),local,sizeof(local))) throw std::runtime_error("source address");
                auto sport = ntohs(number<USHORT>(r,L"sport"));
                printf("net\t%lld\t%lu\t%u\t%s\t%u\t%lu\t%s\t%u\n",t,pid,id,dest,port,size,local,sport);
            }
        } else return;
    } catch (const std::exception&) { knownProcesses.clear(); puts("gap\tdecode"); }
}
static ULONG WINAPI flushed(EVENT_TRACE_LOGFILEW*) { fflush(stdout); return TRUE; }
int wmain(int argc, wchar_t** argv) {
    if (argc!=1) {
        if (argc!=3 || wcscmp(argv[1], L"--only-pid") || !(onlyPid=wcstoul(argv[2], nullptr, 10))) return 2;
    }
    name = L"BridgeOutboundAudit-" + std::to_wstring(GetCurrentProcessId());
    auto v = props();
    ULONG rc = StartTraceW(&session,name.c_str(),&v.p);
    if (rc) { fprintf(stderr,"StartTrace error %lu\n",rc); return 1; }
    SetConsoleCtrlHandler(stop,TRUE);
    auto enable = [](const GUID& provider,ULONGLONG keywords) {
        return EnableTraceEx2(session,&provider,EVENT_CONTROL_CODE_ENABLE_PROVIDER,
                              TRACE_LEVEL_VERBOSE,keywords,0,0,nullptr);
    };
    auto procStatus = enable(processes,0x10); // WINEVENT_KEYWORD_PROCESS
    processProviderMissing = procStatus!=0;
    auto netStatus = enable(network,~0ULL);
    if (procStatus) printf("unavailable\tprocess\t%lu\n",procStatus);
    if (netStatus) printf("unavailable\tnetwork\t%lu\n",netStatus);
    {
        // Enable only socket-connect records. Other AFD events may describe application buffers.
        struct { EVENT_FILTER_EVENT_ID header; USHORT extra[3]; } filter{};
        filter.header.FilterIn = TRUE;
        filter.header.Count = 4;
        USHORT ids[] = {4,5,1018,1021};
        memcpy(filter.header.Events,ids,sizeof(ids));
        EVENT_FILTER_DESCRIPTOR desc{reinterpret_cast<ULONGLONG>(&filter),sizeof(filter),EVENT_FILTER_TYPE_EVENT_ID};
        ENABLE_TRACE_PARAMETERS params{};
        params.Version = ENABLE_TRACE_PARAMETERS_VERSION_2;
        params.EnableFilterDesc = &desc;
        params.FilterDescCount = 1;
        auto attemptStatus = EnableTraceEx2(session,&winsock,EVENT_CONTROL_CODE_ENABLE_PROVIDER,TRACE_LEVEL_VERBOSE,
                                            ~0ULL,0,0,&params);
        if (attemptStatus) printf("unavailable\tconnect\t%lu\n",attemptStatus);
        fflush(stdout);
        rc = (netStatus && attemptStatus) ? netStatus : ERROR_SUCCESS;
    }
    if (!rc) {
        EVENT_TRACE_LOGFILEW log{};
        log.LoggerName = &name[0];
        log.ProcessTraceMode = PROCESS_TRACE_MODE_REAL_TIME | PROCESS_TRACE_MODE_EVENT_RECORD;
        // ProcessTrace converts QPC to FILETIME unless RAW_TIMESTAMP is requested.
        log.EventRecordCallback = consume;
        log.BufferCallback = flushed;
        auto trace = OpenTraceW(&log);
        if (trace == INVALID_PROCESSTRACE_HANDLE) rc = GetLastError();
        else {
            std::atomic<bool> finished(false);
            std::thread heartbeat([&finished]() {
                ULONG lost = 0;
                while (!finished) {
                    auto stats = props();
                    bool pipeClosed = false;
                    if (!ControlTraceW(session,name.c_str(),&stats.p,EVENT_TRACE_CONTROL_QUERY)) {
                        std::lock_guard<std::mutex> lock(evidenceMutex);
                        auto count = stats.p.EventsLost + stats.p.RealTimeBuffersLost;
                        if (count!=lost) { knownProcesses.clear(); puts("gap\tloss"); lost=count; }
                        puts("health"); fflush(stdout);
                        pipeClosed = ferror(stdout)!=0;
                    }
                    if (pipeClosed) { stop(CTRL_BREAK_EVENT); return; }
                    Sleep(1000);
                }
            });
            rc = ProcessTrace(&trace,1,nullptr,nullptr);
            finished = true; heartbeat.join();
            CloseTrace(trace);
        }
    }
    v = props();
    ControlTraceW(session,name.c_str(),&v.p,EVENT_TRACE_CONTROL_STOP);
    if (rc && rc!=ERROR_CANCELLED) { fprintf(stderr,"Trace error %lu\n",rc); return 1; }
    return 0;
}
