#include "../firmware/src/Policy.h"
#include <assert.h>
int main() {
    Move m;
    assert(parseMove("forward 30",m) && m.axis==0 && m.cm==30);
    const char* bad[]={"forward 19","forward 101","forward 030","forward 30 land","rc 0 20 0 0","forward -30","forward 30\n","forward 999999999999"};
    for(auto text:bad) assert(!parseMove(text,m));
    Reading r[4]={{true,900,100},{true,900,100},{true,900,100},{true,900,100}};
    assert(!check(r,110,m,true));
    r[0].mm=800; assert(check(r,110,m,true));
    r[0].mm=801; assert(!check(r,110,m,true));
    r[0].mm=500; assert(check(r,110,m,false));
    r[0].mm=900;
    for(int i=0;i<4;++i) {
        r[i].valid=false; assert(check(r,110,m,true)); r[i].valid=true;
        r[i].mm=300; assert(check(r,110,m,true)); r[i].mm=900;
    }
    assert(check(r,401,m,true));
    for (auto& v:r) v.at=0xfffffff0;
    assert(!check(r,10,m,true)); // millis wrap
    assert(parseMove("right 100",m) && m.axis==3);
}
