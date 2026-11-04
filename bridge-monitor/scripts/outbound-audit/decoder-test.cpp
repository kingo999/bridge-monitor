// Exercise TDH against the installed manifest without enabling any live providers.
#define wmain collector_main
#include "collector.cpp"
#undef wmain

int wmain() {
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
    consume(&record);
    return 0;
}
