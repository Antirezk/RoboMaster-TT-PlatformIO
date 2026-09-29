#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "ToFSensor.h"
#include "Policy.h"

ToFManager sensors;
Reading readings[4];
Move activeMove;
String pending, usbLine, ttLine;
bool ready=false, airborne=false, locked=false, overflowUsb=false, overflowTt=false;
uint32_t lastBeat=0, started=0, sampled=0, reported=0, lastId=0, activeId=0;
uint32_t lastHover=0;
WiFiUDP udp;
IPAddress ownerIp;
uint16_t ownerPort=0;
bool wirelessOwner=false, serialOwner=false;

void emit(const String& line) {
    Serial.println(line);
    if (wirelessOwner) {
        udp.beginPacket(ownerIp,ownerPort);
        udp.print(line); udp.print('\n'); udp.endPacket();
    }
}

void result(uint32_t id, const char* phase, const char* detail) {
    emit("RESULT "+String(id)+" "+phase+" "+detail);
}
void wireSend(const char* text) {
    emit(String("SDK_TX ")+text);
#if TOF_LIVE
    Serial1.printf("[TELLO] %s",text);
#endif
}
void halt(const char* reason) {
    if (locked) return;
    locked=true; ready=false;
    result(activeId,"UNKNOWN",reason);
    // Overlapping SDK commands have no response IDs. Never attribute a late
    // movement OK to this stop. Lock until reboot and physical verification.
    if (airborne || pending=="takeoff") wireSend("stop");
    pending="";
    emit("LOCKED stop-request-unconfirmed; verify-aircraft; land-still-available");
}
void start(const char* command, uint32_t id) {
    pending=command; activeId=id; started=millis(); wireSend(command);
}
void request(uint32_t id, const String& command) {
    if (id<=lastId) { result(id,"REJECTED","duplicate-or-old-id"); return; }
    lastId=id;
    if (command=="status") {
        result(id,"STATUS",locked?"locked":(ready?"ready":"not-ready")); return;
    }
    if (command=="land" && locked) {
        wireSend("land"); result(id,"UNKNOWN","land-request-unconfirmed"); return;
    }
    if ((command=="stop" || command=="land") && pending.length()) {
        halt("operator-interrupt");
        if (command=="land") wireSend("land");
        result(id,"UNKNOWN","interrupt-request-unconfirmed"); return;
    }
    if (locked) { result(id,"REJECTED","locked-reboot-after-verification"); return; }
    if (pending.length()) { result(id,"REJECTED","busy-no-queue"); return; }
    if (command=="command") {
        if (ready) result(id,"DONE","already-ready");
        else start("command",id);
        return;
    }
    if (!ready) { result(id,"REJECTED","sdk-not-ready"); return; }
    Move move;
    bool isMove=parseMove(command.c_str(),move);
    bool takeoff=command=="takeoff";
    if (!isMove && !takeoff && command!="land" && command!="stop" && command!="battery?") {
        result(id,"REJECTED","unsupported-command"); return;
    }
    if (isMove || takeoff) {
        const char* reason=check(readings,millis(),move,true);
        if (reason) { result(id,"REJECTED",reason); return; }
        if (millis()-lastBeat>1500) { result(id,"REJECTED","heartbeat-stale"); return; }
#if TOF_LIVE
        if (isMove && !airborne) { result(id,"REJECTED","takeoff-not-confirmed"); return; }
        if (takeoff && airborne) { result(id,"REJECTED","already-airborne"); return; }
#endif
    }
#if !TOF_LIVE
    if (isMove || takeoff || command=="land" || command=="stop") {
        result(id,"PREVIEW",command.c_str()); return;
    }
#endif
    activeMove=move;
    start(command.c_str(),id);
}
void response(const String& line) {
    emit(String("SDK_RX ")+line);
    if (locked || !pending.length()) return;
    if (line=="ETT ok") {
        if (pending=="command") { start("speed 20",activeId); return; }
        if (pending=="speed 20") ready=true;
        if (pending=="takeoff") airborne=true;
        if (pending=="land") airborne=false;
        result(activeId,"DONE",pending.c_str()); pending=""; activeMove=Move();
    } else if (line.startsWith("ETT error") || line=="ETT unactive") {
        // A failed movement may have moved partially: no automatic continuation.
        halt("sdk-error");
    } else if (pending=="battery?" && line.startsWith("ETT ")) {
        String number=line.substring(4); int n=number.toInt();
        if (number==String(n) && n>=0 && n<=100) {
            result(activeId,"DONE",line.c_str()); pending="";
        }
    }
}
void frame(const String& line) {
    if (line=="PING") { lastBeat=millis(); return; }
    int split=line.indexOf(' ');
    String token=line.substring(0,split);
    uint32_t id=strtoul(token.c_str(),nullptr,10);
    if (split>0 && id>0 && token==String(id)) request(id,line.substring(split+1));
    else emit("PROTOCOL_ERROR invalid-frame");
}
void setup() {
    Serial.begin(115200);
    Serial1.begin(1000000,SERIAL_8N1,23,18);
    Wire.begin(27,26); Wire.setClock(100000); Wire.setTimeOut(20);
    sensors.begin();
    WiFi.mode(WIFI_AP); WiFi.setSleep(false);
    WiFi.softAP("TT-ToF-Demo","RMTT1234"); udp.begin(9000);
    Serial.printf("TOF_DEMO %s USB115200 front=0 back=1 left=4 right=3\n",TOF_LIVE?"LIVE":"BENCH-no-flight");
}
void loop() {
    int packet=udp.parsePacket();
    if (packet>0) {
        char bytes[97]={};
        int n=udp.read(bytes,96);
        if (packet<=95 && n==packet && !serialOwner) {
            String line(bytes); line.trim();
            if (!wirelessOwner && line=="PING") {
                wirelessOwner=true; ownerIp=udp.remoteIP(); ownerPort=udp.remotePort();
            }
            if (wirelessOwner && udp.remoteIP()==ownerIp && udp.remotePort()==ownerPort
                && strlen(bytes)==size_t(n)) frame(line);
        }
    }
    while (Serial.available()) {
        char c=Serial.read();
        if (c=='\n') {
            if (!overflowUsb && !wirelessOwner) {
                if (usbLine=="PING") serialOwner=true;
                if (serialOwner) frame(usbLine);
            } else Serial.println("PROTOCOL_ERROR oversized-frame");
            usbLine=""; overflowUsb=false;
        } else if (c!='\r') {
            if (usbLine.length()<95 && !overflowUsb) usbLine+=c;
            else overflowUsb=true;
        }
    }
#if TOF_LIVE
    while (Serial1.available()) {
        char c=Serial1.read();
        if (c=='\n') {
            if (!overflowTt) response(ttLine);
            else halt("uart-overflow");
            ttLine=""; overflowTt=false;
        } else if (c!='\r') {
            if (ttLine.length()<255 && !overflowTt) ttLine+=c;
            else overflowTt=true;
        }
    }
#else
    if (pending.length()) response(pending=="battery?"?"ETT 85":"ETT ok");
#endif
    uint32_t now=millis();
    if (now-sampled>=50) {
        sensors.update(); sampled=millis(); now=sampled;
        const ToFData* data[]={&sensors.front(),&sensors.rear(),&sensors.left(),&sensors.right()};
        for (int i=0;i<4;++i) {
            const auto& d=*data[i];
            readings[i]={d.status==ToFStatus::VALID && d.raw>0 && d.raw<=2000,
                         uint16_t(min(d.raw,d.filtered)),d.lastUpdate};
        }
    }
    if (!locked && pending!="land" && (airborne || pending=="takeoff")) {
        const char* reason=check(readings,now,activeMove,false);
        if (reason) halt(reason);
        else if (now-lastBeat>1500) halt("pc-heartbeat-lost");
    }
    if (pending.length() && now-started>15000) halt("sdk-timeout-no-retry");
    if (airborne && !locked && !pending.length() && now-lastHover>=100) {
        lastHover=now;
#if TOF_LIVE
        Serial1.print("[TELLO] rc 0 0 0 0");
#endif
    }
    if (now-reported>=500) {
        reported=now;
        char summary[256];
        snprintf(summary,sizeof(summary),"TOF f=%u/%u b=%u/%u l=%u/%u r=%u/%u ready=%u busy=%u locked=%u mode=%s",
            readings[0].mm,readings[0].valid,readings[1].mm,readings[1].valid,
            readings[2].mm,readings[2].valid,readings[3].mm,readings[3].valid,
            ready,pending.length()>0,locked,TOF_LIVE?"live":"bench");
        emit(summary);
    }
}
