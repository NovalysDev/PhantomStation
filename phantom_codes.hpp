#pragma once
// GameShark / Action Replay subset for PS1; files belong to the current game.
#include "atomic_file_write.h"
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace phantom_codes {
constexpr uint32_t ram_size = 2u * 1024u * 1024u;
struct Op {
    uint32_t kind = 0, address = 0, value = 0, width = 0;
    uint32_t count = 0, step = 0, delta = 0, span = 0;
    uint8_t wildcard = 0, find[16] = {}, replace[16] = {};
};
struct Cheat {
    std::string name;
    std::vector<std::string> lines;
    std::vector<Op> ops;
    std::string error;
    bool enabled = false;
};

inline std::string trim(std::string s) {
    const auto nonspace = [](unsigned char c) { return !std::isspace(c); };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), nonspace));
    s.erase(std::find_if(s.rbegin(), s.rend(), nonspace).base(), s.end());
    return s;
}
inline bool hex(const std::string& s, uint32_t& out) {
    if (s.empty() || s.size() > 8) return false;
    out = 0;
    for (unsigned char c : s) {
        int n = c >= '0' && c <= '9' ? c-'0' : c >= 'a' && c <= 'f' ? c-'a'+10 : c >= 'A' && c <= 'F' ? c-'A'+10 : -1;
        if (n < 0) return false;
        out = (out << 4) | static_cast<uint32_t>(n);
    }
    return true;
}
inline bool parse_pair(const std::string& line, uint32_t& a, uint32_t& b, size_t& length) {
    std::istringstream input(line);
    std::string first, second, extra;
    if (!(input >> first >> second) || (input >> extra) || first.size() != 8 || (second.size() != 4 && second.size() != 8)) return false;
    length = second.size();
    return hex(first, a) && hex(second, b);
}
inline bool parse(Cheat& cheat) {
    cheat.ops.clear(); cheat.error.clear();
    if (cheat.lines.empty() || cheat.lines.size() > 200) { cheat.error = "Entre entre 1 et 200 lignes."; return false; }
    for (size_t i = 0; i < cheat.lines.size(); ++i) {
        uint32_t a, b; size_t n;
        if (!parse_pair(cheat.lines[i], a, b, n)) { cheat.error = "Format invalide : attendu 80000000 0000."; return false; }
        Op op; op.kind = a >> 24; op.address = a & 0xffffffu; op.value = b;
        if (op.kind == 0x50) {
            if ((a & 0x00ff0000u) || n != 4 || i+1 >= cheat.lines.size()) { cheat.error = "Répétition incomplète."; return false; }
            uint32_t next, value; size_t len;
            if (!parse_pair(cheat.lines[++i], next, value, len) || len != 4 || (next >> 24 != 0x30 && next >> 24 != 0x80)) { cheat.error = "Répétition non prise en charge."; return false; }
            op.width = next >> 24 == 0x30 ? 1 : 2; op.address = next & 0xffffffu;
            op.count = (a >> 8) & 255; op.step = a & 255; op.delta = b; op.value = value;
            if (!op.count || op.address % op.width || (op.step % op.width) || (op.width == 1 && value > 255) ||
                uint64_t(op.address) + uint64_t(op.count-1)*op.step + op.width > ram_size) { cheat.error = "Répétition hors mémoire."; return false; }
        } else if (op.kind == 0xf4) {
            if (n != 8 || (op.address & 1u) || i+4 >= cheat.lines.size()) { cheat.error = "Recherche/remplacement incomplet."; return false; }
            op.span = (b & 0xffffu) * 4u; op.wildcard = (b >> 16) & 255;
            if (!op.span || op.address >= ram_size) { cheat.error = "Recherche hors mémoire."; return false; }
            std::string packed;
            for (int j=0; j<4; ++j) {
                uint32_t x,y; size_t len;
                if (!parse_pair(cheat.lines[++i],x,y,len) || len != 8) { cheat.error = "Recherche/remplacement invalide."; return false; }
                char buf[17]; std::snprintf(buf,sizeof(buf),"%08X%08X",x,y); packed += buf;
            }
            for (int j=0; j<32; ++j) {
                uint32_t v; if (!hex(packed.substr(j*2,2),v)) return false;
                (j<16 ? op.find[j] : op.replace[j-16]) = static_cast<uint8_t>(v);
            }
        } else {
            switch (op.kind) {
                case 0x30: case 0x31: case 0x32: case 0xe0: case 0xe1: case 0xe2: case 0xe3: op.width=1; break;
                case 0x80: case 0x81: case 0x82: case 0xd0: case 0xd1: case 0xd2: case 0xd3: op.width=2; break;
                case 0x90: case 0x91: case 0x92: case 0xa0: case 0xa1: case 0xa2: case 0xa3: op.width=4; break;
                default: cheat.error = "Ce type de code n'est pas encore pris en charge."; return false;
            }
            if (op.address % op.width || op.address + op.width > ram_size || (op.width == 1 && b > 255) || (op.width == 2 && b > 65535)) { cheat.error = "Adresse ou valeur invalide."; return false; }
        }
        cheat.ops.push_back(op);
    }
    auto type = cheat.ops.back().kind;
    if ((type >= 0xd0 && type <= 0xd3) || (type >= 0xe0 && type <= 0xe3) || (type >= 0xa0 && type <= 0xa3)) { cheat.error = "Condition sans ligne suivante."; return false; }
    return true;
}

inline bool load_file(const std::string& path, std::vector<Cheat>& list, std::string& error) {
    std::ifstream in(path, std::ios::binary);
    if (!in) { error = "Impossible de lire les codes."; return false; }
    in.seekg(0,std::ios::end); const auto len=in.tellg(); in.seekg(0);
    if (len < 0 || len > 512*1024) { error = "Fichier de codes trop volumineux."; return false; }
    std::string row;
    while (std::getline(in,row)) {
        if (!row.empty() && row.back()=='\r') row.pop_back();
        if (row.empty() || row[0]=='#') continue;
        auto a=row.find('\t'), b=a==std::string::npos ? a : row.find('\t',a+1);
        if (a!=1 || b==std::string::npos || (row[0]!='0' && row[0]!='1') || list.size()>=200) { error="Fichier de codes invalide : il n'a pas été modifié."; return false; }
        Cheat c; c.enabled=row[0]=='1'; c.name=row.substr(a+1,b-a-1);
        if (c.name.empty() || c.name.size()>120) { error="Nom de code invalide."; return false; }
        std::istringstream codes(row.substr(b+1)); std::string line;
        while (std::getline(codes,line,'|')) if (!trim(line).empty()) c.lines.push_back(trim(line));
        if (!parse(c)) c.enabled=false;
        list.push_back(std::move(c));
    }
    return true;
}
inline bool load(std::vector<Cheat>& list, std::string& error) {
    list.clear(); error.clear();
    const std::string user="phantom-codes.tsv", bundled="assets/phantom-codes-default.tsv";
    if (std::filesystem::exists(user)) { if (!load_file(user,list,error)) { list.clear(); return false; } return true; }
    if (std::filesystem::exists(bundled)) { if (!load_file(bundled,list,error)) { list.clear(); return false; } return true; }
    return true;
}
inline bool save(const std::vector<Cheat>& list, std::string& error) {
    std::string data="# Codes propres à ce jeu. Une ligne par code.\n";
    for (const auto& c : list) {
        data += c.enabled ? "1\t" : "0\t"; data += c.name + '\t';
        for (size_t i=0;i<c.lines.size();++i) { if (i) data += '|'; data += c.lines[i]; }
        data += '\n';
    }
    return psx::atomic_write_file("phantom-codes.tsv",data,&error);
}

template<class Read, class Write>
inline void apply(const Cheat& cheat, Read read, Write write, bool scan) {
    auto read_value=[&](uint32_t a,uint32_t width) { uint32_t v=0; for(uint32_t i=0;i<width;++i) v|=uint32_t(read(a+i))<<(8*i); return v; };
    auto write_value=[&](uint32_t a,uint32_t width,uint32_t v) { for(uint32_t i=0;i<width;++i) { auto b=uint8_t(v>>(8*i)); if(read(a+i)!=b) write(a+i,b); } };
    bool skip=false;
    for (const auto& op : cheat.ops) {
        if (skip) { skip=false; continue; }
        if ((op.kind>=0xd0 && op.kind<=0xd3)||(op.kind>=0xe0 && op.kind<=0xe3)||(op.kind>=0xa0 && op.kind<=0xa3)) {
            uint32_t v=read_value(op.address,op.width); int t=op.kind&3;
            skip=!(t==0?v==op.value:t==1?v!=op.value:t==2?v<op.value:v>op.value);
        } else if (op.kind==0xf4) {
            if (!scan) continue;
            uint32_t start=op.address>op.span/2 ? op.address-op.span/2 : 0;
            uint32_t end=std::min(ram_size,op.address+(op.span+1)/2);
            for (uint32_t p=start;p+16<=end;++p) {
                bool match=true;
                for(int i=0;i<16 && match;++i) if(op.find[i]!=op.wildcard && read(p+i)!=op.find[i]) match=false;
                if(match) for(int i=0;i<16;++i) if(op.replace[i]!=op.wildcard && read(p+i)!=op.replace[i]) write(p+i,op.replace[i]);
            }
        } else if (op.kind==0x50) {
            for(uint32_t i=0;i<op.count;++i) write_value(op.address+i*op.step,op.width,op.value+i*op.delta);
        } else {
            uint32_t v=op.value;
            if(op.kind==0x31 || op.kind==0x81 || op.kind==0x91) v=read_value(op.address,op.width)|v;
            if(op.kind==0x32 || op.kind==0x82 || op.kind==0x92) v=read_value(op.address,op.width)&~v;
            write_value(op.address,op.width,v);
        }
    }
}
} // namespace phantom_codes
