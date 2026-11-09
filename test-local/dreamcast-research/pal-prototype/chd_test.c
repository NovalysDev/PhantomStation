#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <io.h>
int pal_chd_open(const char *path);
int pal_chd_sector(uint32_t lba, unsigned char out[2048]);
int main(int argc, char **argv) {
    unsigned char data[2048];
    if (argc != 3 || pal_chd_open(argv[1]) || pal_chd_sector((uint32_t)strtoul(argv[2], NULL, 10), data)) return 1;
    _setmode(_fileno(stdout), _O_BINARY);
    return fwrite(data, 1, sizeof data, stdout) == sizeof data ? 0 : 1;
}
