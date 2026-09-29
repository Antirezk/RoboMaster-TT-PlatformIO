#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Body axes: front, back, left, right. All distances are millimetres.
struct Reading { bool valid; uint16_t mm; uint32_t at; };
struct Move { int axis = -1; int cm = 0; };
inline bool parseMove(const char* text, Move& out) {
    const char* names[] = {"forward", "back", "left", "right"};
    for (int i=0;i<4;++i) {
        const size_t n = strlen(names[i]);
        if (strncmp(text,names[i],n) || text[n]!=' ') continue;
        const char* p=text+n+1;
        if (*p<'1' || *p>'9') return false;
        int value=0, digits=0;
        while (*p>='0' && *p<='9') {
            if (++digits>3) return false;
            value=value*10+(*p++-'0');
        }
        if (*p || value<20 || value>100) return false;
        out.axis=i; out.cm=value; return true;
    }
    return false;
}
inline const char* check(const Reading* r, uint32_t now, const Move& m, bool admission) {
    for (int i=0;i<4;++i) {
        if (!r[i].valid || uint32_t(now-r[i].at)>300) return "sensor-invalid-or-stale";
        // Side/rear clearance also required; no autonomous escape movement.
        if (r[i].mm<=300) return "perimeter-clearance";
    }
    // 50 cm residual clearance includes a provisional braking margin.
    if (m.axis>=0 && r[m.axis].mm <= 500+(admission?m.cm*10:0))
        return "path-clearance";
    return nullptr;
}
