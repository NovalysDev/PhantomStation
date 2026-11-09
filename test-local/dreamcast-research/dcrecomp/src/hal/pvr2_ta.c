/**
 * PowerVR2 Tile Accelerator FIFO Parser
 *
 * Receives 32-byte packets from Store Queue flushes or direct TA writes,
 * parses them according to the PVR2 TA protocol, and builds triangle
 * lists for the OpenGL renderer.
 */

#include "hal/pvr2.h"
#include "hal/dc_hardware.h"
#include "recompiler/sh4_cpu.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

static struct {
    /* Current polygon header state */
    uint32_t pcw;
    uint32_t isp;
    uint32_t tsp;
    uint32_t tcw;
    int current_list;
    int vert_type;
    int vert_size;       /* 32 or 64 bytes */

    /* Face color (for intensity vertex types) */
    float face_r, face_g, face_b, face_a;

    /* Strip → triangle conversion */
    PVR2RenderVertex strip_prev[2];
    int strip_count;

    /* 64-byte parameter buffering */
    uint32_t pending[8];
    uint32_t sprite_color;   /* Sprite Base Color, from the header */
    int awaiting_second;   /* 0=no, 1=poly header, 2=vertex */

    /* Triangle buffers per list type */
    PVR2TriList lists[PVR2_NUM_LISTS];

    /* Statistics */
    int total_vertices;
    int total_polygons;
    int total_strips;
    int packets_received;

    /* Logging */
    int log_countdown;
} g_ta;


/* ========== Helpers ========== */

static void unpack_color(uint32_t packed, float *r, float *g, float *b, float *a) {
    *a = ((packed >> 24) & 0xFF) / 255.0f;
    *r = ((packed >> 16) & 0xFF) / 255.0f;
    *g = ((packed >>  8) & 0xFF) / 255.0f;
    *b = ((packed >>  0) & 0xFF) / 255.0f;
}
static float ta_color_unit(float value) {
    return !isfinite(value) || value < 0.0f ? 0.0f : value > 1.0f ? 1.0f : value;
}

static void emit_triangle_vertex(const PVR2RenderVertex *v) {
    int list = g_ta.current_list;
    if (list < 0 || list >= PVR2_NUM_LISTS) return;

    PVR2TriList *tl = &g_ta.lists[list];
    if (tl->count >= tl->capacity) return;

    tl->vertices[tl->count] = *v;
    tl->vertices[tl->count].pcw = g_ta.pcw;
    tl->vertices[tl->count].isp = g_ta.isp;
    tl->vertices[tl->count].tsp = g_ta.tsp;
    tl->vertices[tl->count].tcw = g_ta.tcw;
    tl->count++;
}

/* Faces cachées, comme la vraie carte graphique (mode dans les bits 28-27 du
 * mot ISP/TSP) : 0 aucune, 1 les triangles minuscules, 2 aussi ceux dont l'aire
 * à l'écran est négative, 3 aussi ceux dont elle est positive. Le rendu n'en
 * cachait aucune. Crazy Taxi dessine chaque voiture en deux moitiés, la
 * seconde étant le reflet de la première avec le mode inverse : sans ce tri,
 * on voyait de dos le feu arrière d'un côté et l'intérieur de la calandre de
 * l'autre. PHANTOM_DC_SANS_FACES_CACHEES=1 retire ce tri. */
static int triangle_cache(const PVR2RenderVertex *a, const PVR2RenderVertex *b, const PVR2RenderVertex *c) {
    static int off = -1;
    if (off < 0) off = getenv("PHANTOM_DC_SANS_FACES_CACHEES") != NULL;
    unsigned mode = (g_ta.isp >> 27) & 3u;
    if (off || !mode) return 0;
    float aire = (a->x - c->x) * (b->y - c->y) - (b->x - c->x) * (a->y - c->y);
    union { uint32_t u; float f; } seuil;
    seuil.u = dc_hw_read32(sh4_get_hardware(), 0x005F8078u);   /* FPU_CULL_VAL */
    float petit = isfinite(seuil.f) ? fabsf(seuil.f) : 0.0f;
    { static int on = -1; static unsigned petite, signe, total;
      if (on < 0) on = getenv("DCRECOMP_CULL_STATS") != NULL;
      if (on) {
          total++;
          if (fabsf(aire) < petit) petite++;
          else if ((mode == 2 && aire < 0.0f) || (mode == 3 && aire > 0.0f)) signe++;
          if ((total % 20000u) == 0)
              printf("[CULL] %u triangles, %u culled by threshold (%.3f), %u by sign, mode=%u\n",
                     total, petite, petit, signe, mode);
      } }
    if (fabsf(aire) < petit) return 1;
    return (mode == 2 && aire < 0.0f) || (mode == 3 && aire > 0.0f);
}

static void process_strip_vertex(const PVR2RenderVertex *v, int end_of_strip) {
    if (g_ta.strip_count >= 2) {
        /* Emit triangle from strip */
        const PVR2RenderVertex *a = &g_ta.strip_prev[0], *b = &g_ta.strip_prev[1];
        if (g_ta.strip_count & 1) { a = &g_ta.strip_prev[1]; b = &g_ta.strip_prev[0]; }
        if (!triangle_cache(a, b, v)) {
            emit_triangle_vertex(a);
            emit_triangle_vertex(b);
            emit_triangle_vertex(v);
        }
        g_ta.total_polygons++;
    }

    g_ta.strip_prev[0] = g_ta.strip_prev[1];
    g_ta.strip_prev[1] = *v;
    g_ta.strip_count++;
    g_ta.total_vertices++;

    if (end_of_strip) {
        g_ta.strip_count = 0;
        g_ta.total_strips++;
    }
}


/* ========== Vertex Type Determination ========== */

static int ta_get_vert_type(uint32_t pcw) {
    int para_type = (pcw >> 29) & 7;
    int list_type = (pcw >> 24) & 7;
    int texture   = (pcw >>  3) & 1;
    int col_type  = (pcw >>  4) & 3;
    int volume    = (pcw >>  6) & 1;
    int uv_16bit  = pcw & 1;

    /* Modifier volumes */
    if (list_type == PVR2_LIST_OPAQUE_MOD || list_type == PVR2_LIST_TRANS_MOD)
        return 17;

    /* Sprites */
    if (para_type == TA_PARAM_SPRITE)
        return texture ? 16 : 15;

    /* Two-volume */
    if (volume) {
        if (texture) {
            if (col_type == 0) return uv_16bit ? 12 : 11;
            return uv_16bit ? 14 : 13;
        }
        if (col_type == 0) return 9;
        return 10;
    }

    /* Textured */
    if (texture) {
        if (col_type == 0) return uv_16bit ? 4 : 3;
        if (col_type == 1) return uv_16bit ? 6 : 5;
        return uv_16bit ? 8 : 7;
    }

    /* Non-textured */
    if (col_type == 0) return 0;
    if (col_type == 1) return 1;
    return 2;
}

static int ta_vert_size(int vert_type) {
    switch (vert_type) {
    case 5: case 6:      /* Textured, float color */
    case 11: case 12:    /* Two-volume, textured, packed */
    case 13: case 14:    /* Two-volume, textured, intensity */
    case 15: case 16:    /* Sprite */
        return 64;
    default:
        return 32;
    }
}

static int ta_poly_size(uint32_t pcw) {
    int col_type = (pcw >> 4) & 3;
    int volume   = (pcw >> 6) & 1;
    int offset   = (pcw >> 2) & 1;
    int texture  = (pcw >> 3) & 1;

    /* Intensity + textured + offset → 64 bytes */
    if (col_type == 2 && texture && offset) return 64;
    /* Two-volume + intensity → 64 bytes */
    if (volume && col_type == 2) return 64;

    return 32;
}


/* ========== Parameter Processing ========== */

/* The list a game is currently filling. Like the real TA, the list type is
 * taken from the first global parameter after a list starts (TA_LIST_INIT,
 * TA_LIST_CONT or the previous End Of List) and the field is ignored in the
 * ones that follow. An End Of List carries no list type of its own, so the
 * interrupt it raises is the latched list's. Following every header instead
 * sent Grandia II's translucent modifier list - modifier volumes copied from
 * the opaque one, only the first header retyped - to the opaque modifier
 * bit; Kamui never saw that list end and never started the render again. */
static int open_list = 0, list_latched = 0;

static void ta_process_polygon(const uint32_t *data, int size) {
    g_ta.pcw = data[0];
    g_ta.isp = data[1];
    g_ta.tsp = data[2];
    g_ta.tcw = data[3];
    if (!list_latched) { open_list = (g_ta.pcw >> 24) & 7; list_latched = 1; }
    g_ta.current_list = open_list;
    g_ta.vert_type = ta_get_vert_type(g_ta.pcw);
    g_ta.vert_size = ta_vert_size(g_ta.vert_type);
    g_ta.strip_count = 0;

    /* A sprite's colour is in its header, not its vertices. */
    if (((g_ta.pcw >> 29) & 7) == TA_PARAM_SPRITE)
        g_ta.sprite_color = data[4];

    /* Extract face color for intensity mode */
    int col_type = (g_ta.pcw >> 4) & 3;
    /* Type 3 reuses the previous face color; its header has no colors. */
    if (col_type == 2) {
        union { uint32_t u; float f; } c;
        if (size >= 64) {
            c.u = data[8];  g_ta.face_a = ta_color_unit(c.f);
            c.u = data[9];  g_ta.face_r = ta_color_unit(c.f);
            c.u = data[10]; g_ta.face_g = ta_color_unit(c.f);
            c.u = data[11]; g_ta.face_b = ta_color_unit(c.f);
        } else {
            c.u = data[4]; g_ta.face_a = ta_color_unit(c.f);
            c.u = data[5]; g_ta.face_r = ta_color_unit(c.f);
            c.u = data[6]; g_ta.face_g = ta_color_unit(c.f);
            c.u = data[7]; g_ta.face_b = ta_color_unit(c.f);
        }
    }
}

/* TA diagnostic counters (file scope) */
static int type_counts[8];
static int eos_count;
static int list_type_hist[8];

static int vert_log = 0;

static void ta_process_vertex(const uint32_t *data) {
    PVR2RenderVertex v = {0};
    union { uint32_t u; float f; } c;
    int end_of_strip = (data[0] >> 28) & 1;
    if (end_of_strip) eos_count++;

    if (vert_log < 10) {
        vert_log++;
        union { uint32_t u; float f; } x, y, z;
        x.u = data[1]; y.u = data[2]; z.u = data[3];
        printf("[VERT] #%d type=%d eos=%d PCW=0x%08X pos=(%.1f, %.1f, %.1f) list=%d strip=%d\n",
               vert_log, g_ta.vert_type, end_of_strip, data[0],
               x.f, y.f, z.f, g_ta.current_list, g_ta.strip_count);
    }

    { static int on = -1, n; static uint32_t seen[64]; static int seen_n;
      if (on < 0) on = getenv("DCRECOMP_UV_PROBE") != NULL;
      if (on && n < 120 && (g_ta.pcw & 8u) && ((g_ta.pcw >> 29) & 7u) == 4u) {
          int known = 0;
          for (int k = 0; k < seen_n; ++k) if (seen[k] == g_ta.pcw) known = 1;
          if (!known && seen_n < 64) {
              seen[seen_n++] = g_ta.pcw; n++;
              printf("[UV] type=%d pcw=%08X bit0=%u bit1=%u w4=%08X w5=%08X w6=%08X\n",
                     g_ta.vert_type, g_ta.pcw, g_ta.pcw & 1u, (g_ta.pcw >> 1) & 1u,
                     data[4], data[5], data[6]);
          }
      } }
    switch (g_ta.vert_type) {
    case 0: /* Non-textured, packed color */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        unpack_color(data[6], &v.r, &v.g, &v.b, &v.a);
        break;

    case 1: /* Non-textured, float RGBA */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[4]; v.a = c.f;
        c.u = data[5]; v.r = c.f;
        c.u = data[6]; v.g = c.f;
        c.u = data[7]; v.b = c.f;
        break;

    case 2: /* Non-textured, intensity */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[6]; {
            float intensity = ta_color_unit(c.f);
            v.r = g_ta.face_r * intensity;
            v.g = g_ta.face_g * intensity;
            v.b = g_ta.face_b * intensity;
            v.a = g_ta.face_a;
        }
        break;

    case 3: /* Textured, packed color, 32-bit UV */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[4]; v.u = c.f;
        c.u = data[5]; v.v = c.f;
        unpack_color(data[6], &v.r, &v.g, &v.b, &v.a);
        break;

    case 4: /* Textured, packed color, 16-bit UV */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[4] & 0xffff0000u; v.u = c.f;
        c.u = data[4] << 16; v.v = c.f;
        unpack_color(data[6], &v.r, &v.g, &v.b, &v.a);
        break;

    case 5: /* Textured, float RGBA, 32-bit UV (64-byte) */
    case 6: /* Textured, float RGBA, 16-bit UV (64-byte) */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        if (g_ta.vert_type == 5) {
            c.u = data[4]; v.u = c.f;
            c.u = data[5]; v.v = c.f;
        } else {
            c.u = data[4] & 0xffff0000u; v.u = c.f;
            c.u = data[4] << 16; v.v = c.f;
        }
        /* Colors in second 32 bytes (data[8..11]) */
        c.u = data[8];  v.a = c.f;
        c.u = data[9];  v.r = c.f;
        c.u = data[10]; v.g = c.f;
        c.u = data[11]; v.b = c.f;
        break;

    case 7: /* Textured, intensity, 32-bit UV */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[4]; v.u = c.f;
        c.u = data[5]; v.v = c.f;
        c.u = data[6]; {
            float intensity = ta_color_unit(c.f);
            v.r = g_ta.face_r * intensity;
            v.g = g_ta.face_g * intensity;
            v.b = g_ta.face_b * intensity;
            v.a = g_ta.face_a;
        }
        break;

    case 8: /* Textured, intensity, 16-bit UV */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        c.u = data[4] & 0xffff0000u; v.u = c.f;
        c.u = data[4] << 16; v.v = c.f;
        c.u = data[6]; {
            float intensity = ta_color_unit(c.f);
            v.r = g_ta.face_r * intensity;
            v.g = g_ta.face_g * intensity;
            v.b = g_ta.face_b * intensity;
            v.a = g_ta.face_a;
        }
        break;

    case 15:   /* Sprite, untextured */
    case 16: { /* Sprite, textured */
        /* The fourth vertex carries only x/y; reuse C's depth. */
        PVR2RenderVertex q[4];
        static const int off[4][3] = { {1,2,3}, {4,5,6}, {7,8,9}, {10,11,9} };
        for (int k = 0; k < 4; k++) {
            q[k] = (PVR2RenderVertex){0};
            c.u = data[off[k][0]]; q[k].x = c.f;
            c.u = data[off[k][1]]; q[k].y = c.f;
            c.u = data[off[k][2]]; q[k].z = c.f;
            unpack_color(g_ta.sprite_color, &q[k].r, &q[k].g, &q[k].b, &q[k].a);
        }
        if (g_ta.vert_type == 16) {
            for (int k = 0; k < 3; k++) {
                c.u = data[13 + k] & 0xffff0000u; q[k].u = c.f;
                c.u = data[13 + k] << 16; q[k].v = c.f;
            }
            q[3].u=q[0].u+q[2].u-q[1].u;
            q[3].v=q[0].v+q[2].v-q[1].v;
        }
        /* Two triangles, sharing the A-C diagonal. */
        emit_triangle_vertex(&q[0]); emit_triangle_vertex(&q[1]);
        emit_triangle_vertex(&q[2]);
        emit_triangle_vertex(&q[0]); emit_triangle_vertex(&q[2]);
        emit_triangle_vertex(&q[3]);
        g_ta.total_polygons += 2;
        g_ta.total_vertices += 4;
        g_ta.total_strips++;
        return;
    }

    default:
        /* Unsupported vertex type - use white with position */
        c.u = data[1]; v.x = c.f;
        c.u = data[2]; v.y = c.f;
        c.u = data[3]; v.z = c.f;
        v.r = v.g = v.b = v.a = 1.0f;
        break;
    }

    { static int on = -1, n;
      if (on < 0) on = getenv("DCRECOMP_UV_PCW") != NULL;
      if (on && n < 60 && (g_ta.pcw == 0x808C00BCu || g_ta.pcw == 0x808C00BEu)) {
          n++;
          printf("[UVP] pcw=%08X type=%d raw=%08X,%08X,%08X uv=%.3f,%.3f col=%.2f,%.2f,%.2f\n",
                 g_ta.pcw, g_ta.vert_type, data[4], data[5], data[6], v.u, v.v, v.r, v.g, v.b);
      } }
    process_strip_vertex(&v, end_of_strip);
}


/* ========== Packet Entry Point ========== */

static void ta_process_packet(const uint32_t *data) {
    int para_type = (data[0] >> 29) & 7;
    type_counts[para_type]++;

    switch (para_type) {
    case TA_PARAM_END_OF_LIST: {
        /* One interrupt per list type, and the game waits for the one
         * belonging to the list it just filled. Opaque, opaque modifier,
         * translucent and translucent modifier are consecutive; punch-through
         * sits on its own bit. */
        static const int eol_bit[8] = { 7, 8, 9, 10, 21, 21, 21, 21 };
        { static int on = -1; static unsigned n;
          if (on < 0) on = getenv("PHANTOM_DC_TA_EOL_TRACE") != NULL;
          if (on && n++ < 400000)
              printf("[TA-EOL] list %d -> bit %d (%d strips)\n", open_list, eol_bit[open_list & 7], g_ta.strip_count); }
        dc_hw_raise_istnrm(eol_bit[open_list & 7]);
        g_ta.strip_count = 0;
        list_latched = 0;
        break;
    }

    case TA_PARAM_USER_CLIP:
        /* TODO: implement user clipping */
        break;

    case TA_PARAM_POLYGON:
    case TA_PARAM_SPRITE: {
        int lt = (data[0] >> 24) & 7;
        list_type_hist[lt]++;
        int poly_size = ta_poly_size(data[0]);
        if (poly_size == 64) {
            memcpy(g_ta.pending, data, 32);
            g_ta.awaiting_second = 1;
        } else {
            ta_process_polygon(data, 32);
        }
        break;
    }

    case TA_PARAM_VERTEX:
        if (g_ta.vert_size == 64) {
            memcpy(g_ta.pending, data, 32);
            g_ta.awaiting_second = 2;
        } else {
            ta_process_vertex(data);
        }
        break;

    default:
        /* Unknown parameter type - ignore */
        break;
    }
}


/* ========== Public API ========== */

void pvr2_ta_init(void) {
    memset(&g_ta, 0, sizeof(g_ta));
    g_ta.face_r = g_ta.face_g = g_ta.face_b = g_ta.face_a = 1.0f;

    for (int i = 0; i < PVR2_NUM_LISTS; i++) {
        g_ta.lists[i].capacity = PVR2_MAX_VERTICES;
        g_ta.lists[i].vertices = (PVR2RenderVertex *)calloc(
            PVR2_MAX_VERTICES, sizeof(PVR2RenderVertex));
        g_ta.lists[i].count = 0;
    }

    g_ta.current_list = -1;
    g_ta.log_countdown = 60;  /* Log stats for first 60 frames */
    printf("[PVR2] TA initialized\n");
}

void pvr2_ta_destroy(void) {
    for (int i = 0; i < PVR2_NUM_LISTS; i++) {
        free(g_ta.lists[i].vertices);
        g_ta.lists[i].vertices = NULL;
    }
}

static int ta_write_log = 0;

void pvr2_ta_write(const uint32_t *data) {
    g_ta.packets_received++;
    if (ta_write_log < 3) {
        ta_write_log++;
        int para_type = (data[0] >> 29) & 7;
        printf("[TA] packet #%d: PCW=0x%08X para_type=%d list=%d\n",
               g_ta.packets_received, data[0], para_type, (data[0] >> 24) & 7);
    }

    if (g_ta.awaiting_second) {
        /* Combine with pending data into full 64 bytes */
        uint32_t full[16];
        memcpy(full, g_ta.pending, 32);
        memcpy(full + 8, data, 32);
        int was_type = g_ta.awaiting_second;
        g_ta.awaiting_second = 0;

        if (was_type == 1) {
            ta_process_polygon(full, 64);
        } else {
            ta_process_vertex(full);
        }
    } else {
        ta_process_packet(data);
    }
}

/* Diagnostic: preserve a completed list across the next TA_LIST_INIT. */
static PVR2TriList g_saved_lists[PVR2_NUM_LISTS];
static int g_saved_ready = 0, g_saved_selected = 0;
void pvr2_ta_test_save_list(void) {
    if (!g_ta.total_vertices) return;
    for (int i = 0; i < PVR2_NUM_LISTS; ++i) {
        int n = g_ta.lists[i].count;
        if (n > g_saved_lists[i].capacity) {
            PVR2RenderVertex *p = realloc(g_saved_lists[i].vertices, (size_t)n * sizeof(*p));
            if (!p) { g_saved_ready = 0; return; }
            g_saved_lists[i].vertices = p; g_saved_lists[i].capacity = n;
        }
        if (n) memcpy(g_saved_lists[i].vertices, g_ta.lists[i].vertices, (size_t)n * sizeof(PVR2RenderVertex));
        g_saved_lists[i].count = n;
    }
    g_saved_ready = 1;
}
int pvr2_ta_test_select_saved(int select) {
    if (select && !g_saved_ready) return 0;
    g_saved_selected = select;
    if (!select) g_saved_ready = 0;
    return 1;
}

static int g_ta_has_drawn = 0;

int pvr2_ta_has_drawn(void) { return g_ta_has_drawn; }

void pvr2_ta_reset(void) {
    if (g_ta.total_vertices > 0) g_ta_has_drawn = 1;
    /* Report the first frame that actually carries geometry: the interesting
     * moment is when a game stops submitting empty lists. */
    { static int announced = 0;
      if (!announced && g_ta.total_vertices > 0) {
          announced = 1;
          printf("[PVR2] first frame with geometry: %d pkts, %d verts, %d polys\n",
                 g_ta.packets_received, g_ta.total_vertices, g_ta.total_polygons);
      } }
    /* Log stats for first 3 frames */
    static unsigned frame_no = 0;
    if (g_ta.log_countdown > 57 || (++frame_no % 150) == 0) {
        printf("[PVR2] TA frame stats: %d pkts, %d verts, %d polys, %d strips\n",
               g_ta.packets_received, g_ta.total_vertices,
               g_ta.total_polygons, g_ta.total_strips);
        printf("[PVR2]   type breakdown: end=%d clip=%d _=%d _=%d poly=%d sprite=%d _=%d vert=%d\n",
               type_counts[0], type_counts[1], type_counts[2], type_counts[3],
               type_counts[4], type_counts[5], type_counts[6], type_counts[7]);
        printf("[PVR2]   eos_count=%d list_types: %d %d %d %d %d %d %d %d\n",
               eos_count, list_type_hist[0], list_type_hist[1], list_type_hist[2],
               list_type_hist[3], list_type_hist[4], list_type_hist[5],
               list_type_hist[6], list_type_hist[7]);
        printf("[PVR2]   List verts: opaque=%d trans=%d punchthru=%d\n",
               g_ta.lists[PVR2_LIST_OPAQUE].count,
               g_ta.lists[PVR2_LIST_TRANS].count,
               g_ta.lists[PVR2_LIST_PUNCHTHRU].count);
    }
    g_ta.log_countdown--;
    memset(type_counts, 0, sizeof(type_counts));
    memset(list_type_hist, 0, sizeof(list_type_hist));
    eos_count = 0;

    for (int i = 0; i < PVR2_NUM_LISTS; i++) {
        g_ta.lists[i].count = 0;
    }
    g_ta.strip_count = 0;
    g_ta.awaiting_second = 0;
    g_ta.total_vertices = 0;
    g_ta.total_polygons = 0;
    g_ta.total_strips = 0;
    g_ta.packets_received = 0;
    g_ta.current_list = -1;
    list_latched = 0;
}

/* TA_LIST_CONT: a new list starts on top of what is already there. */
void pvr2_ta_list_continue(void) { list_latched = 0; }

const PVR2TriList* pvr2_ta_get_list(int list_type) {
    if (list_type < 0 || list_type >= PVR2_NUM_LISTS) return NULL;
    return g_saved_selected ? &g_saved_lists[list_type] : &g_ta.lists[list_type];
}

void pvr2_ta_get_stats(int *packets, int *vertices, int *polygons) {
    if (packets)  *packets  = g_ta.packets_received;
    if (vertices) *vertices = g_ta.total_vertices;
    if (polygons) *polygons = g_ta.total_polygons;
}
