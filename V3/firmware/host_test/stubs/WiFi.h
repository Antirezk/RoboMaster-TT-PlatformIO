#pragma once
#include "IPAddress.h"
#define WIFI_AP 2
struct WiFiClass {
    bool mode(int) { return true; }
    bool setSleep(bool) { return true; }
    bool softAP(const char*, const char*) { return true; }
};
extern WiFiClass WiFi;
