/* Independent, opt-in VMU storage used by the Dreamcast research prototype. */
#include "hal/dc_vmu.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define VMU_SIZE (256u * 512u)
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <wchar.h>
static void mkpath_w(const wchar_t *path) {
    wchar_t tmp[1024]; size_t n = wcslen(path);
    if (!n || n >= 1024) return;
    wcscpy(tmp, path);
    for (size_t i = 1; i < n; ++i) {
        if (tmp[i] == (wchar_t)92 || tmp[i] == L'/') {
            wchar_t c = tmp[i]; tmp[i] = 0;
            if (tmp[i-1] != L':') _wmkdir(tmp);
            tmp[i] = c;
        }
    }
}
#include <windows.h>
static int card_has_save_w(const wchar_t *path) {
    FILE *f = _wfopen(path, L"rb");
    unsigned char buf[512]; int found = 0;
    if (!f) return 0;
    for (unsigned b = 0; b < 241 && !found; ++b) {
        if (fseek(f, (long)b * 512, SEEK_SET)) break;
        if (fread(buf, 1, sizeof buf, f) != sizeof buf) break;
        for (unsigned k = 0; k < sizeof buf; ++k) if (buf[k]) { found = 1; break; }
    }
    fclose(f);
    return found;
}
/* Le dossier du jeu change a chaque nouveau paquet : la carte de ce dossier
 * est donc vierge et la partie repart du debut. On reprend la carte la plus
 * recente du meme titre qui contient vraiment une sauvegarde. */
int dc_vmu_recover_w(const wchar_t *wanted) {
    wchar_t path[1024], root[1024], title[512], pat[1024], best[1024];
    wchar_t *cut; size_t n = 0;
    WIN32_FIND_DATAW fd; HANDLE h; long long best_time = -1;
    if (!wanted || wcslen(wanted) >= 1024) return 0;
    wcscpy(path, wanted);
    cut = wcsrchr(path, (wchar_t)92); if (!cut) cut = wcsrchr(path, L'/');
    if (!cut) return 0;
    *cut = 0;
    wcscpy(root, path);
    cut = wcsrchr(root, (wchar_t)92); if (!cut) cut = wcsrchr(root, L'/');
    if (!cut) return 0;
    *cut = 0; cut += 1;
    while (cut[n] && n < 511 && !(cut[n] == L' ' && cut[n+1] == L'-' && cut[n+2] == L' ')) { title[n] = cut[n]; ++n; }
    title[n] = 0;
    if (!n) return 0;
    swprintf(pat, 1024, L"%ls/%ls - *", root, title);
    h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        wchar_t card[1024];
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        swprintf(card, 1024, L"%ls/%ls/carte-dreamcast.vmu", root, fd.cFileName);
        if (!card_has_save_w(card)) continue;
        long long t = ((long long)fd.ftLastWriteTime.dwHighDateTime << 32) | fd.ftLastWriteTime.dwLowDateTime;
        if (t > best_time) { best_time = t; wcscpy(best, card); }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    if (best_time < 0) return 0;
    if (!dc_vmu_open_w(best)) return 0;
    printf("[VMU] Sauvegarde reprise : %ls", best); printf("");
    return 1;
}
int dc_vmu_autoopen_w(const wchar_t *wanted) {
    if (wanted && card_has_save_w(wanted) && dc_vmu_open_w(wanted)) return 1;
    if (dc_vmu_recover_w(wanted)) return 1;
    return dc_vmu_open_w(wanted);
}
#else
#include <sys/types.h>
static void mkpath(const char *path) {
    char tmp[1024]; size_t n = strlen(path);
    if (!n || n >= 1024) return;
    strcpy(tmp, path);
    for (size_t i = 1; i < n; ++i) {
        if (tmp[i] == '/') { char c = tmp[i]; tmp[i] = 0; mkdir(tmp, 0777); tmp[i] = c; }
    }
}
#endif
static FILE *file;
static uint8_t flash[VMU_SIZE];
static void put16(uint8_t *p, unsigned v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void make_blank(void) {
    memset(flash,0,sizeof flash);
    uint8_t *root=flash+255u*512u, *fat=flash+254u*512u;
    memset(root,0x55,16);
    put16(root+0x40,255); put16(root+0x42,0); put16(root+0x44,255);
    put16(root+0x46,254); put16(root+0x48,1); put16(root+0x4a,253);
    put16(root+0x4c,13); put16(root+0x50,200); put16(root+0x52,31);
    put16(root+0x54,0); put16(root+0x56,128);
    for (unsigned i=0;i<256;i++) put16(fat+2*i,0xfffc);
    put16(fat+2*255,0xfffa); put16(fat+2*254,0xfffa);
    for (unsigned i=253; i>241; --i) put16(fat+2*i,i-1);
    put16(fat+2*241,0xfffa);
}
int dc_vmu_open(const char *path) {
    if (!path || !*path || file) return 0;
    file=fopen(path,"rb+");
    if (file) {
        if (fseek(file,0,SEEK_END) || ftell(file)!=VMU_SIZE || fseek(file,0,SEEK_SET) ||
            fread(flash,1,VMU_SIZE,file)!=VMU_SIZE) { fclose(file); file=NULL; return 0; }
        return 1;
    }
    /* A pre-existing file that cannot be opened must never be truncated : on
     * ne cree le fichier que s'il est reellement absent, et on cree son dossier
     * au passage (sinon l'ouverture echoue et le jeu ne voit aucune carte). */
#ifndef _WIN32
    mkpath(path);
#endif
    file=fopen(path,"wbx+");
    if (!file) return 0;
    make_blank();
    if (fwrite(flash,1,VMU_SIZE,file)!=VMU_SIZE || fflush(file)) {
        fclose(file); file=NULL; return 0;
    }
    return 1;
}
#ifdef _WIN32
/* Environment paths arrive as UTF-16 on Windows; never round-trip through the
 * active ANSI code page, which would lose accents or non-Latin characters. */
int dc_vmu_open_w(const wchar_t *path) {
    if (!path || !*path || file) return 0;
    file=_wfopen(path,L"rb+");
    if (file) {
        if (fseek(file,0,SEEK_END) || ftell(file)!=VMU_SIZE || fseek(file,0,SEEK_SET) ||
            fread(flash,1,VMU_SIZE,file)!=VMU_SIZE) { fclose(file); file=NULL; return 0; }
        return 1;
    }
    mkpath_w(path);
    int fd = -1;
    if (_wsopen_s(&fd, path, _O_CREAT | _O_EXCL | _O_RDWR | _O_BINARY, _SH_DENYNO, _S_IREAD | _S_IWRITE) != 0) return 0;
    file = _wfdopen(fd, L"wb+");
    if (!file) { _close(fd); return 0; }
    if (!file) return 0;
    make_blank();
    if (fwrite(flash,1,VMU_SIZE,file)!=VMU_SIZE || fflush(file)) {
        fclose(file); file=NULL; return 0;
    }
    return 1;
}
#endif
void dc_vmu_close(void) { if(file) fclose(file); file=NULL; }
int dc_vmu_active(void) { return file != NULL; }
int dc_vmu_read(unsigned block, uint8_t out[512]) {
    if (!file || !out || block>=256) return 0;
    memcpy(out,flash+block*512u,512); return 1;
}
int dc_vmu_write(unsigned block, unsigned phase, const uint8_t *data, size_t size) {
    if (!file || !data || block>=256 || phase>=4 || !size || size>128) return 0;
    unsigned offset=block*512u+phase*128u;
    if (fseek(file,(long)offset,SEEK_SET) || fwrite(data,1,size,file)!=size || fflush(file)) return 0;
    memcpy(flash+offset,data,size); return 1;
}
