/* GD-ROM CHD reader for the experimental diagnostic only. */
#include <libchdr/chd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint32_t start, end, stored; int type; } Track;
static Track tracks[99];
static unsigned ntracks, per_hunk, hunk_bytes;
static uint32_t cached = UINT32_MAX;
static chd_file *disc;
static unsigned char *buffer;

static int pal_chd_finish_open(chd_error err) {
    if (err != CHDERR_NONE) { fprintf(stderr, "[PAL-CHD] %s\n", chd_error_string(err)); return -1; }
    const chd_header *header = chd_get_header(disc);
    if (!header || !header->hunkbytes || header->hunkbytes % 2448) return -1;
    hunk_bytes = header->hunkbytes; per_hunk = hunk_bytes / 2448;
    buffer = malloc(hunk_bytes);
    if (!buffer) return -1;
    uint32_t lba = 0, stored = 0;
    for (unsigned i = 0; i < 99; i++) {
        char meta[512], type[32], subtype[32], pgtype[32], pgsub[32];
        int number, frames, pad, pregap, postgap;
        uint32_t len;
        err = chd_get_metadata(disc, GDROM_TRACK_METADATA_TAG, i, meta, sizeof(meta)-1, &len, NULL, NULL);
        if (err == CHDERR_METADATA_NOT_FOUND) break;
        if (err != CHDERR_NONE || len >= sizeof(meta)) return -1;
        meta[len] = 0;
        if (sscanf(meta, GDROM_TRACK_METADATA_FORMAT, &number, type, subtype, &frames,
                   &pad, &pregap, pgtype, pgsub, &postgap) != 9 || number != (int)i + 1 ||
            frames <= 0 || pad < 0 || pad > frames || pregap < 0) return -1;
        int in_file = pregap && pgtype[0] == 'V' ? pregap : 0;
        if (in_file > frames) return -1;
        uint32_t start = lba + (uint32_t)pregap;
        tracks[i] = (Track){start, start + (uint32_t)(frames - in_file - pad),
                            stored + (uint32_t)in_file,
                            !strcmp(type,"MODE1_RAW") ? 1 : !strcmp(type,"MODE1") ? 2 : 0};
        lba = start + (uint32_t)(frames - in_file);
        stored += ((uint32_t)frames + 3u) & ~3u;
        ntracks++;
    }
    if (ntracks < 3) return -1;
    fprintf(stderr, "[PAL-CHD] %u pistes GD-ROM ouvertes\n", ntracks);
    return 0;
}

int pal_chd_open(const char *path) {
    return pal_chd_finish_open(chd_open(path,CHD_OPEN_READ,NULL,&disc));
}
int pal_chd_open_stream(FILE *stream) {
    if (!stream) return -1;
    /* libchdr does not own this stream; it stays open for the game process. */
    return pal_chd_finish_open(chd_open_file(stream,CHD_OPEN_READ,NULL,&disc));
}

int pal_chd_sector(uint32_t lba, unsigned char out[2048]) {
    for (unsigned i=0; i<ntracks; i++) {
        Track t=tracks[i];
        if (lba < t.start || lba >= t.end) continue;
        if (!t.type) return -1;
        uint32_t frame=t.stored + lba - t.start, hunk=frame / per_hunk;
        if (cached != hunk) {
            if (chd_read(disc, hunk, buffer) != CHDERR_NONE) return -1;
            cached=hunk;
        }
        size_t pos=(frame % per_hunk)*2448u + (t.type == 1 ? 16u : 0u);
        if (pos + 2048 > hunk_bytes) return -1;
        memcpy(out, buffer + pos, 2048);
        return 0;
    }
    return -1;
}

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}

/* Read one ISO9660 root file without extracting the disc to the filesystem. */
int pal_chd_root_file(const char *name, unsigned char *dest, uint32_t capacity) {
    unsigned char sector[2048];
    if (pal_chd_sector(45016, sector) || sector[0] != 1 || memcmp(sector+1,"CD001",5)) return -1;
    uint32_t root_lba=le32(sector+158), root_size=le32(sector+166);
    if (!root_size || root_size > 8*1024*1024u) return -1;
    for (uint32_t off=0; off<root_size; off+=2048) {
        if (pal_chd_sector(root_lba + off/2048, sector)) return -1;
        for (unsigned p=0; p<2048 && off+p<root_size;) {
            unsigned n=sector[p];
            if (!n) break;
            if (n<34 || p+n>2048 || 33u+sector[p+32]>n) return -1;
            unsigned len=sector[p+32], wanted=(unsigned)strlen(name);
            if (!(sector[p+25]&2) && len>=wanted &&
                !memcmp(sector+p+33,name,wanted) && (len==wanted || sector[p+33+wanted]==';')) {
                uint32_t file_lba=le32(sector+p+2), size=le32(sector+p+10);
                if (size>capacity) return -1;
                for (uint32_t j=0; j<size; j+=2048) {
                    unsigned char data[2048];
                    if (pal_chd_sector(file_lba + j/2048, data)) return -1;
                    uint32_t count=size-j < 2048 ? size-j : 2048;
                    memcpy(dest+j,data,count);
                }
                return (int)size;
            }
            p+=n;
        }
    }
    return -1;
}
