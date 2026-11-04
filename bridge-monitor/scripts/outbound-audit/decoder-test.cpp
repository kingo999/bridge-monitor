// Exercise TDH against the installed manifest without enabling any live providers.
#define wmain collector_main
#include "collector.cpp"
#undef wmain

int wmain(int argc, wchar_t** argv) {
    EVENT_RECORD record{};
    record.EventHeader.Size = sizeof(EVENT_HEADER);
    record.EventHeader.Flags = EVENT_HEADER_FLAG_64_BIT_HEADER;
    record.EventHeader.ProviderId = network;
    record.EventHeader.EventDescriptor.Id = 10;
    record.EventHeader.TimeStamp.QuadPart = 134000000000000000LL;
    // Microsoft-Windows-Kernel-Network TCP IPv4 send v0 manifest fields.
    struct Payload {
        ULONG pid, size;
        BYTE dst[4], src[4];
        USHORT dport, sport;
        ULONG start, end, seq, conn;
    } payload{4242,321,{192,0,2,8},{192,0,2,1},htons(443),htons(65000),0,0,0,0};
    record.UserData = &payload;
    record.UserDataLength = sizeof(payload);
    if (argc > 1) {
        // The sender existed before tracing began; no process-start event was received.
        onlyPid = GetCurrentProcessId();
        payload.pid = onlyPid;
        FILETIME now{};
        GetSystemTimeAsFileTime(&now);
        ULARGE_INTEGER ticks{};
        ticks.LowPart=now.dwLowDateTime; ticks.HighPart=now.dwHighDateTime;
        record.EventHeader.TimeStamp.QuadPart = ticks.QuadPart;
        if (!wcscmp(argv[1], L"--child-process")) {
            // A child starts after tracing, but its parent predates collection.
#pragma pack(push,1)
            struct StartPayload {
                ULONG pid; ULONGLONG created; ULONG parent, session; wchar_t image[10];
            } start{4000000000UL,ticks.QuadPart,GetCurrentProcessId(),0,L"child.exe"};
#pragma pack(pop)
            onlyPid=0;
            record.EventHeader.ProviderId=processes;
            record.EventHeader.EventDescriptor.Id=1;
            record.UserData=&start;
            record.UserDataLength=sizeof(start);
            consume(&record);
            return 0;
        }
    }
    consume(&record);
    return 0;
}
