// Parity harness: reads records [u8 mode][i8 district][u32 len][text] from stdin file, prints analyzePost results TSV
#include "analyzer.h"
#include "gen_data.h"
#include <stdio.h>
#include <string.h>
#include <vector>
static const CityDef* city(const char* id) { for (int i = 0; i < CITY_COUNT; i++) if (!strcmp(CITIES[i].id, id)) return &CITIES[i]; return nullptr; }
int main(int argc, char** argv) {
  FILE* f = fopen(argv[1], "rb");
  const CityDef* k = city("kremenchuk");
  std::vector<char> buf;
  unsigned char mode; signed char d; unsigned int len; long n = 0;
  while (fread(&mode, 1, 1, f) == 1 && fread(&d, 1, 1, f) == 1 && fread(&len, 4, 1, f) == 1) {
    buf.assign(len + 1, 0);
    if (fread(buf.data(), 1, len, f) != len) break;
    bool remote = mode & 1; int home = (mode & 2) ? 1 : 0;
    PostEval e = analyzePost(buf.data(), k, d, remote, home);
    const char* th = e.threat != 255 ? THREATS[e.threat].type : "";
    printf("%ld\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\n", n++, e.score, (int)e.level, e.near, e.cls, e.prox, e.clear,
           e.ignored, e.relevant, e.far, e.local, e.explicitCity, e.oblastWide, th);
  }
  return 0;
}
