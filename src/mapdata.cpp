// Растр мапи районів (згенеровано tools/gen_map.py) — єдине місце, де підключається map_data.h
#include "map_data.h"

// Наближені види (області, околиці міст) — tools/gen_map_views.py
#include "map_views_meta.h"
#include "map_views.h"
// вид «уся Україна» поверх старого растру
extern const MapView MAP_VIEW_UA = {MAP_PROJ_MINX, MAP_PROJ_MINY, MAP_PROJ_SCALE, MAP_PROJ_OFFX, MAP_PROJ_OFFY,
                                    MAP_ROW_OFS, MAP_RUNS, nullptr, 0, 0, 0, MAP_W, MAP_H};
