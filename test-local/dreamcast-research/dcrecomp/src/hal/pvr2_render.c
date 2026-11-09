/**
 * PowerVR2 OpenGL Renderer
 *
 * Renders PVR2 triangle lists using OpenGL 3.3 core.
 * Currently renders colored triangles without texturing.
 */

#include "hal/pvr2.h"
#include "hal/pvr2_texture.h"
#include "hal/pvr2_viewport.h"
#include "hal/dc_hardware.h"
#include "hal/dc_ralentis.h"
#include "recompiler/sh4_cpu.h"
#include <stdlib.h>
#include <stdio.h>
#include <math.h>
#include <string.h>

#ifdef HAS_OPENGL

#include <GL/glew.h>

static GLuint g_shader;
static GLuint g_vao;
static GLuint g_vbo;
static GLuint g_render_fbo, g_render_color, g_render_depth;
static int g_render_width=640, g_render_height=480;
static GLint  g_uniform_screen_size;
static GLint  g_uniform_textured;
static GLint  g_uniform_shading, g_uniform_alpha, g_uniform_ignore_tex_alpha;
static GLint  g_uniform_clamp_u, g_uniform_clamp_v, g_uniform_flip_u, g_uniform_flip_v, g_uniform_show_uv;
static GLint  g_uniform_punch, g_uniform_punch_ref;
/* Textures gardées d'une image à l'autre. Avant, le cache était vidé à chaque
 * image : chaque texture était redécodée et renvoyée à la carte graphique à
 * chaque fois, près d'un tiers du temps de House of the Dead 2 en jeu. Une
 * texture n'est refaite que si l'empreinte de ce qu'elle lit en mémoire vidéo
 * a changé (pvr2_texture_fingerprint) ; quand tout est plein, on remplace celle
 * servie le moins récemment. */
#define PVR_TEXTURE_CACHE_MAX 2048
#define PVR_TEXTURE_BUCKETS 4096
static struct { GLuint id; uint32_t tsp, tcw; uint64_t print; unsigned seen; int valid, w, h, next; } g_textures[PVR_TEXTURE_CACHE_MAX];
static int g_texture_bucket[PVR_TEXTURE_BUCKETS];   /* indice + 1 ; 0 = vide */
static unsigned g_texture_count, g_texture_frame;
static unsigned texture_bucket(uint32_t tsp, uint32_t tcw) {
    return ((tsp * 2654435761u) ^ (tcw * 2246822519u)) >> 20 & (PVR_TEXTURE_BUCKETS - 1u);
}
static int texture_find(uint32_t tsp, uint32_t tcw) {
    for (int i = g_texture_bucket[texture_bucket(tsp, tcw)] - 1; i >= 0; i = g_textures[i].next - 1)
        if (g_textures[i].tsp == tsp && g_textures[i].tcw == tcw) return i;
    return -1;
}
static int texture_new(uint32_t tsp, uint32_t tcw) {
    int k;
    if (g_texture_count < PVR_TEXTURE_CACHE_MAX) k = (int)g_texture_count++;
    else {
        k = 0;
        for (int i = 1; i < PVR_TEXTURE_CACHE_MAX; i++) if (g_textures[i].seen < g_textures[k].seen) k = i;
        int *link = &g_texture_bucket[texture_bucket(g_textures[k].tsp, g_textures[k].tcw)];
        while (*link && *link - 1 != k) link = &g_textures[*link - 1].next;
        if (*link) *link = g_textures[k].next;
    }
    g_textures[k].tsp = tsp; g_textures[k].tcw = tcw; g_textures[k].seen = 0; g_textures[k].valid = 0;
    unsigned b = texture_bucket(tsp, tcw);
    g_textures[k].next = g_texture_bucket[b]; g_texture_bucket[b] = k + 1;
    return k;
}
static unsigned char *g_texture_pixels;
typedef struct { float depth; int index; } PVR2SortIndex;
static PVR2SortIndex *g_sort_indices;
static PVR2RenderVertex *g_sorted_vertices;
static size_t g_sort_capacity;

/* A smaller reciprocal depth is farther away. Preserve submission order for
 * equal depths. Like Flycast's PVR sort, use the triangle's smallest 1/w:
 * https://github.com/flyinghead/flycast/blob/master/core/hw/pvr/ta_util.cpp */
static int compare_translucent(const void *left, const void *right) {
    const PVR2SortIndex *a=left, *b=right;
    if (a->depth < b->depth) return -1;
    if (a->depth > b->depth) return 1;
    return (a->index > b->index) - (a->index < b->index);
}
static const PVR2RenderVertex *sort_translucent(const PVR2TriList *list) {
    size_t triangles=(size_t)list->count/3u;
    if (!triangles || list->count%3) return list->vertices;
    if (triangles > g_sort_capacity) {
        PVR2SortIndex *indices=realloc(g_sort_indices,triangles*sizeof(*indices));
        if (!indices) return list->vertices;
        g_sort_indices=indices;
        PVR2RenderVertex *vertices=realloc(g_sorted_vertices,triangles*3u*sizeof(*vertices));
        if (!vertices) return list->vertices;
        g_sorted_vertices=vertices;g_sort_capacity=triangles;
    }
    for (size_t i=0;i<triangles;++i) {
        const PVR2RenderVertex *v=&list->vertices[i*3u];
        float a=isfinite(v[0].z)?v[0].z:0.0f, b=isfinite(v[1].z)?v[1].z:0.0f, c=isfinite(v[2].z)?v[2].z:0.0f;
        g_sort_indices[i].depth=fminf(a,fminf(b,c));g_sort_indices[i].index=(int)i;
    }
    qsort(g_sort_indices,triangles,sizeof(*g_sort_indices),compare_translucent);
    for (size_t i=0;i<triangles;++i)
        memcpy(&g_sorted_vertices[i*3u],&list->vertices[(size_t)g_sort_indices[i].index*3u],3u*sizeof(*g_sorted_vertices));
    return g_sorted_vertices;
}
static int    g_width = 640;
static int    g_height = 480;

/* Vertex shader: PVR2 screen-space coords → NDC. The reciprocal-depth
 * reconstruction and depth range follow Flycast's GPL-2.0 PVR renderer:
 * https://github.com/flyinghead/flycast/blob/master/core/rend/gles/gles.cpp */
static const char *vert_shader_src =
    "#version 330 core\n"
    "layout(location = 0) in vec3 aPos;\n"
    "layout(location = 1) in vec2 aUV;\n"
    "layout(location = 2) in vec4 aColor;\n"
    "out vec4 vColor;\n"
    "out vec3 vUV;\n"
    "uniform vec2 uScreenSize;\n"
    "void main() {\n"
    "    float x = (aPos.x / uScreenSize.x) * 2.0 - 1.0;\n"
    "    float y = 1.0 - (aPos.y / uScreenSize.y) * 2.0;\n"
    "    float z = max(aPos.z, 0.000001);\n"
    "    gl_Position = vec4(x, y, 0.0, 1.0);\n"
    "    vColor = aColor;\n"
    "    vUV = vec3(aUV * z, z);\n"
    "}\n";

/* Fragment shader: pass-through color */
static const char *frag_shader_src =
    "#version 330 core\n"
    "in vec4 vColor;\n"
    "in vec3 vUV;\n"
    "uniform sampler2D uTexture;\n"
    "uniform bool uTextured;\n"
    "uniform int uShading;\n"
    "uniform bool uUseAlpha;\n"
    "uniform bool uIgnoreTexAlpha;\n"
    "uniform bool uPunch;\n"
    "uniform float uPunchRef;\n"
    "uniform bool uClampU;\n"
    "uniform bool uClampV;\n"
    "uniform bool uFlipU;\n"
    "uniform bool uFlipV;\n"
    "uniform bool uShowUV;\n"
    "out vec4 FragColor;\n"
    /* La carte n'a que trois façons de lire hors de la texture : répéter,
     * refléter (bits 17-18 du TSP) et bloquer au bord à 1,0 (bits 15-16). Le
     * blocage ne vaut que par le haut : une coordonnée négative est toujours
     * répétée, ce qu'OpenGL ne sait pas exprimer, d'où ce calcul par fragment.
     * Crazy Taxi reflète ainsi la moitié gauche de chaque voiture. */
    "vec2 adresse_texture(vec2 uv) {\n"
    "    vec2 demi = 0.5 / vec2(textureSize(uTexture, 0));\n"
    "    if (uClampU) uv.x = min(uv.x, 1.0 - demi.x);\n"
    "    else if (uFlipU) uv.x = 1.0 - abs(fract(uv.x * 0.5) * 2.0 - 1.0);\n"
    "    if (uClampV) uv.y = min(uv.y, 1.0 - demi.y);\n"
    "    else if (uFlipV) uv.y = 1.0 - abs(fract(uv.y * 0.5) * 2.0 - 1.0);\n"
    "    return uv;\n"
    "}\n"
    "void main() {\n"
    "    vec4 color = vColor;\n"
    "    if (!uUseAlpha) color.a = 1.0;\n"
    "    if (uTextured) {\n"
    "      vec2 uv = adresse_texture(vUV.xy / max(vUV.z, 0.000001));\n"
    "      vec4 texel = texture(uTexture, uv);\n"
    "      if (uIgnoreTexAlpha) texel.a = 1.0;\n"
    "      if (uShowUV) color = vec4(fract(uv.x), fract(uv.y), 0.0, 1.0);\n"
    "      else if (uShading == 0) color = texel;\n"
    "      else if (uShading == 1) { color.rgb *= texel.rgb; color.a = texel.a; }\n"
    "      else if (uShading == 2) color.rgb = mix(color.rgb, texel.rgb, texel.a);\n"
    "      else color *= texel;\n"
    "    }\n"
    "    if (uPunch && floor(color.a * 255.0 + 0.5) < uPunchRef) discard;\n"
    "    if (uPunch) color.a = 1.0;\n"
    "    gl_FragDepth = log2(1.0 + max(vUV.z, 0.0) * 100000.0) / 34.0;\n"
    "    FragColor = color;\n"
    "}\n";

static GLuint compile_shader(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);

    GLint ok;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetShaderInfoLog(shader, sizeof(log), NULL, log);
        fprintf(stderr, "[PVR2] Shader compile error: %s\n", log);
    }
    return shader;
}

int pvr2_render_init(int width, int height) {
    g_width = width;
    g_height = height;

    /* Initialize GLEW */
    glewExperimental = GL_TRUE;
    GLenum err = glewInit();
    if (err != GLEW_OK) {
        fprintf(stderr, "[PVR2] GLEW init failed: %s\n", glewGetErrorString(err));
        return -1;
    }
    /* Clear any GL error from glewInit */
    while (glGetError() != GL_NO_ERROR) {}

    printf("[PVR2] OpenGL %s (%s)\n",
           glGetString(GL_VERSION), glGetString(GL_RENDERER));

    /* Create shader program */
    GLuint vs = compile_shader(GL_VERTEX_SHADER, vert_shader_src);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, frag_shader_src);

    g_shader = glCreateProgram();
    glAttachShader(g_shader, vs);
    glAttachShader(g_shader, fs);
    glLinkProgram(g_shader);

    GLint ok;
    glGetProgramiv(g_shader, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetProgramInfoLog(g_shader, sizeof(log), NULL, log);
        fprintf(stderr, "[PVR2] Shader link error: %s\n", log);
        return -1;
    }

    glDeleteShader(vs);
    glDeleteShader(fs);

    g_uniform_screen_size = glGetUniformLocation(g_shader, "uScreenSize");
    g_uniform_textured = glGetUniformLocation(g_shader, "uTextured");
    g_uniform_shading = glGetUniformLocation(g_shader, "uShading");
    g_uniform_alpha = glGetUniformLocation(g_shader, "uUseAlpha");
    g_uniform_ignore_tex_alpha = glGetUniformLocation(g_shader, "uIgnoreTexAlpha");
    g_uniform_clamp_u = glGetUniformLocation(g_shader, "uClampU");
    g_uniform_clamp_v = glGetUniformLocation(g_shader, "uClampV");
    g_uniform_flip_u = glGetUniformLocation(g_shader, "uFlipU");
    g_uniform_flip_v = glGetUniformLocation(g_shader, "uFlipV");
    g_uniform_show_uv = glGetUniformLocation(g_shader, "uShowUV");
    g_uniform_punch = glGetUniformLocation(g_shader, "uPunch");
    g_uniform_punch_ref = glGetUniformLocation(g_shader, "uPunchRef");
    g_texture_pixels = malloc(1024u * 1024u * 4u);

    /* Create VAO/VBO */
    glGenVertexArrays(1, &g_vao);
    glGenBuffers(1, &g_vbo);

    glBindVertexArray(g_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);

    /* Vertex layout: PVR2RenderVertex = { x,y,z, u,v, r,g,b,a } */
    /* Position (vec3) at location 0, offset 0 */
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE,
        sizeof(PVR2RenderVertex), (void*)0);
    glEnableVertexAttribArray(0);

    /* UV (vec2) at location 1, offset 12 */
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE,
        sizeof(PVR2RenderVertex), (void*)(3 * sizeof(float)));
    glEnableVertexAttribArray(1);

    /* Color (vec4) at location 2, offset 20 */
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE,
        sizeof(PVR2RenderVertex), (void*)(5 * sizeof(float)));
    glEnableVertexAttribArray(2);

    glBindVertexArray(0);

    /* Render internally at the chosen scale, independently of the size of the
     * parent window. The finished image is then letterboxed into that window. */
    int scale=1; const char *scale_env=getenv("PHANTOM_DC_SCALE");
    if (scale_env) { int n=atoi(scale_env); if(n>=1 && n<=4) scale=n; }
    g_render_width=640*scale; g_render_height=480*scale;
    glGenFramebuffers(1,&g_render_fbo); glBindFramebuffer(GL_FRAMEBUFFER,g_render_fbo);
    glGenTextures(1,&g_render_color); glBindTexture(GL_TEXTURE_2D,g_render_color);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,g_render_width,g_render_height,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
    glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,g_render_color,0);
    glGenRenderbuffers(1,&g_render_depth); glBindRenderbuffer(GL_RENDERBUFFER,g_render_depth);
    glRenderbufferStorage(GL_RENDERBUFFER,GL_DEPTH_COMPONENT24,g_render_width,g_render_height);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,g_render_depth);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr,"[PVR2] Internal render framebuffer unavailable\n"); return -1;
    }
    glBindFramebuffer(GL_FRAMEBUFFER,0);
    printf("[PVR2] Internal resolution %dx%d\n",g_render_width,g_render_height);
    /* Initial GL state */
    pvr2_render_resize(width, height);
    glClearColor(0.0f, 0.0f, 0.1f, 1.0f);
    glDisable(GL_CULL_FACE);
    glEnable(GL_DEPTH_TEST);
    glClearDepth(0.0);
    glDepthFunc(GL_GEQUAL);

    printf("[PVR2] Renderer initialized (%dx%d)\n", width, height);
    return 0;
}

void pvr2_render_destroy(void) {
    if(g_render_depth) { glDeleteRenderbuffers(1,&g_render_depth);g_render_depth=0; }
    if(g_render_color) { glDeleteTextures(1,&g_render_color);g_render_color=0; }
    if(g_render_fbo) { glDeleteFramebuffers(1,&g_render_fbo);g_render_fbo=0; }
    free(g_texture_pixels); g_texture_pixels = NULL;
    free(g_sort_indices); g_sort_indices=NULL;
    free(g_sorted_vertices); g_sorted_vertices=NULL;g_sort_capacity=0;
    for (unsigned i=0; i<PVR_TEXTURE_CACHE_MAX; i++)
        if (g_textures[i].id) { glDeleteTextures(1, &g_textures[i].id); g_textures[i].id=0; }
    memset(g_textures, 0, sizeof g_textures); memset(g_texture_bucket, 0, sizeof g_texture_bucket);
    g_texture_count = 0;
    if (g_vbo) { glDeleteBuffers(1, &g_vbo); g_vbo = 0; }
    if (g_vao) { glDeleteVertexArrays(1, &g_vao); g_vao = 0; }
    if (g_shader) { glDeleteProgram(g_shader); g_shader = 0; }
}


/* ---- Framebuffer presentation ------------------------------------------ */

static GLuint g_fb_tex = 0;
static GLuint g_fb_shader = 0;
static GLuint g_fb_vao = 0, g_fb_vbo = 0;
static unsigned char *g_fb_pixels = NULL;
static int g_fb_logged = 0;

static const char *fb_vert_src =
    "#version 330 core\n"
    "layout(location=0) in vec2 pos;\n"
    "out vec2 uv;\n"
    "void main(){ uv = vec2((pos.x+1.0)*0.5, 1.0-(pos.y+1.0)*0.5);\n"
    "             gl_Position = vec4(pos,0.0,1.0); }\n";

static const char *fb_frag_src =
    "#version 330 core\n"
    "in vec2 uv; out vec4 frag; uniform sampler2D fb;\n"
    "void main(){ frag = texture(fb, uv); }\n";

static void fb_init(void) {
    if (g_fb_tex)
        return;
    GLuint vs = compile_shader(GL_VERTEX_SHADER, fb_vert_src);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, fb_frag_src);
    g_fb_shader = glCreateProgram();
    glAttachShader(g_fb_shader, vs);
    glAttachShader(g_fb_shader, fs);
    glLinkProgram(g_fb_shader);
    glDeleteShader(vs);
    glDeleteShader(fs);

    static const float quad[] = { -1,-1,  1,-1,  -1, 1,   1,-1,  1, 1,  -1, 1 };
    glGenVertexArrays(1, &g_fb_vao);
    glGenBuffers(1, &g_fb_vbo);
    glBindVertexArray(g_fb_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_fb_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof quad, quad, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * sizeof(float), (void *)0);

    glGenTextures(1, &g_fb_tex);
    glBindTexture(GL_TEXTURE_2D, g_fb_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
}

int pvr2_present_framebuffer(uint32_t fb_addr, uint32_t fb_ctrl, int width, int height) {
    const uint8_t *vram = sh4_get_vram_ptr();
    if (!vram || width <= 0 || height <= 0)
        return 0;

    fb_init();
    if (!g_fb_pixels)
        g_fb_pixels = (unsigned char *)malloc(1024 * 1024 * 4);
    if (!g_fb_pixels)
        return 0;

    /* FB_R_CTRL bits 2:0 select the scanout format. */
    int fmt = (int)(fb_ctrl & 0x7);
    uint32_t base = fb_addr & 0x00FFFFFF;
    if (base >= DC_VRAM_SIZE)
        base = 0;

    int nonzero = 0;
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            uint32_t r = 0, g = 0, b = 0;
            if (fmt == 0 || fmt == 1) {                 /* RGB555 / RGB565 */
                uint32_t off = base + (uint32_t)(y * width + x) * 2;
                if (off + 1 >= DC_VRAM_SIZE) continue;
                uint16_t p = (uint16_t)(vram[dc_vram32(off)] | (vram[dc_vram32(off + 1)] << 8));
                if (fmt == 0) {
                    r = ((p >> 10) & 0x1F) << 3;
                    g = ((p >> 5) & 0x1F) << 3;
                    b = (p & 0x1F) << 3;
                } else {
                    r = ((p >> 11) & 0x1F) << 3;
                    g = ((p >> 5) & 0x3F) << 2;
                    b = (p & 0x1F) << 3;
                }
                if (p) nonzero++;
            } else if (fmt == 2) {                      /* RGB888, 3 bytes */
                uint32_t off = base + (uint32_t)(y * width + x) * 3;
                if (off + 2 >= DC_VRAM_SIZE) continue;
                b = vram[dc_vram32(off)]; g = vram[dc_vram32(off + 1)]; r = vram[dc_vram32(off + 2)];
                if (r | g | b) nonzero++;
            } else {                                    /* 0888 / 8888 */
                uint32_t off = base + (uint32_t)(y * width + x) * 4;
                if (off + 3 >= DC_VRAM_SIZE) continue;
                b = vram[dc_vram32(off)]; g = vram[dc_vram32(off + 1)]; r = vram[dc_vram32(off + 2)];
                if (r | g | b) nonzero++;
            }
            unsigned char *px = g_fb_pixels + ((size_t)y * width + x) * 4;
            px[0] = (unsigned char)r; px[1] = (unsigned char)g;
            px[2] = (unsigned char)b; px[3] = 255;
        }
    }

    /* If the scanout window is blank, say whether VRAM holds anything at all
     * and where - during bring-up "nothing on screen" and "nothing anywhere"
     * are very different problems. */
    if (g_fb_logged < 3 && nonzero == 0) {
        size_t used = 0, first = (size_t)-1;
        for (size_t i = 0; i < DC_VRAM_SIZE; i += 4) {
            if (*(const uint32_t *)(vram + i)) {
                used++;
                if (first == (size_t)-1) first = i;
            }
        }
        printf("[PVR2] VRAM: %zu non-zero words, first at 0x%06zX\n", used, first == (size_t)-1 ? (size_t)0 : first);
    }

    if (g_fb_logged < 3) {
        g_fb_logged++;
        printf("[PVR2] present: addr 0x%06X fmt %d %dx%d, %d non-black pixels\n",
               base, fmt, width, height, nonzero);
    }

    /* Nothing in the framebuffer. Draw it anyway and we paint black over the
     * frame the tile accelerator just rendered - two presenters, one window,
     * and the empty one wins because it runs last. Leave the rendered frame
     * where it is; the caller skips its swap on a zero return. */
    if (!nonzero)
        return 0;

    glBindTexture(GL_TEXTURE_2D, g_fb_tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, g_fb_pixels);

    glDisable(GL_DEPTH_TEST);
    glUseProgram(g_fb_shader);
    glBindVertexArray(g_fb_vao);
    glBindTexture(GL_TEXTURE_2D, g_fb_tex);
    glDrawArrays(GL_TRIANGLES, 0, 6);

    return nonzero;
}

int pvr2_screenshot(const char *path) {
    unsigned char *px = (unsigned char *)malloc((size_t)g_width * g_height * 3);
    if (!px)
        return -1;
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, g_width, g_height, GL_RGB, GL_UNSIGNED_BYTE, px);

    FILE *f = fopen(path, "wb");
    if (!f) { free(px); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", g_width, g_height);
    /* OpenGL hands back the bottom row first; a PPM starts at the top. */
    for (int y = g_height - 1; y >= 0; y--)
        fwrite(px + (size_t)y * g_width * 3, 1, (size_t)g_width * 3, f);
    fclose(f);
    free(px);
    printf("[PVR2] wrote %s (%dx%d)\n", path, g_width, g_height);
    return 0;
}

/* Diagnostic temporaire : garde les triangles vus dans une zone de l'écran
 * pendant toute la partie, sans ralentir le jeu par des écritures continues. */
static char *g_trace_buffer; static size_t g_trace_length, g_trace_capacity;
static void pvr2_trace_flush(void) {
    const char *path = getenv("DCRECOMP_TRACE_FILE");
    if (!path || !g_trace_buffer) return;
    FILE *f = fopen(path, "wb");
    if (f) { fwrite(g_trace_buffer, 1, g_trace_length, f); fclose(f); }
    if (getenv("DCRECOMP_TRACE_VERBOSE")) fprintf(stderr, "[TRACE] %zu octets écrits\n", g_trace_length);
}
static void pvr2_trace_zone_line(unsigned frame, int list, int textured, int w, int h,
                                 uint32_t pcw, uint32_t tsp, uint32_t tcw,
                                 float x0,float y0,float x1,float y1,
                                 float u0,float u1,float v0,float v1,
                                 float ua,float va,float ub,float vb,float uc,float vc,
                                 float ax,float ay,float bx,float by,float cx,float cy) {
    static int registered;
    if (!registered) { registered = 1; atexit(pvr2_trace_flush); }
    if (g_trace_length + 512 > g_trace_capacity) {
        size_t next = g_trace_capacity ? g_trace_capacity * 2 : (1u << 20);
        char *grown = realloc(g_trace_buffer, next);
        if (!grown) return;
        g_trace_buffer = grown; g_trace_capacity = next;
    }
    int n = snprintf(g_trace_buffer + g_trace_length, g_trace_capacity - g_trace_length,
                     "[TRI] frame=%u list=%d tex=%d %dx%d pcw=%08X tsp=%08X tcw=%08X box=%.0f,%.0f..%.0f,%.0f uv=%.3f..%.3f,%.3f..%.3f abc=%.3f,%.3f %.3f,%.3f %.3f,%.3f xy=%.1f,%.1f %.1f,%.1f %.1f,%.1f\n",
                     frame, list, textured, w, h, pcw, tsp, tcw, x0,y0,x1,y1, u0,u1,v0,v1,
                     ua,va,ub,vb,uc,vc, ax,ay,bx,by,cx,cy);
    if (n > 0) g_trace_length += (size_t)n;
}


/* ---- Bandeau a l'ecran (« PRESS START ») ------------------------------
 * Le moteur n'avait aucun systeme de texte. Une petite police 5x7 est
 * embarquee ici et dessinee par son propre programme GL, par-dessus l'image
 * du jeu, dans la zone de fenetre ou le jeu est affiche. */
const char *dc_cinematic_message(void);

#include <string.h>

typedef struct { float x, y, u, v; } OsdSommet;

#define OSD_COLS 16
#define OSD_CW   6
#define OSD_CH   8
#define OSD_TEXW (OSD_COLS * OSD_CW)
#define OSD_TEXH (3 * OSD_CH)

static GLuint g_osd_shader, g_osd_vao, g_osd_vbo, g_osd_tex;
static GLint g_osd_couleur = -1, g_osd_police = -1;

static const char *osd_vert_src =
    "#version 330 core\n"
    "layout(location=0) in vec2 pos;\n"
    "layout(location=1) in vec2 uv;\n"
    "out vec2 t;\n"
    "void main(){ t = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n";

static const char *osd_frag_src =
    "#version 330 core\n"
    "in vec2 t; out vec4 f;\n"
    "uniform sampler2D police; uniform vec4 uCouleur;\n"
    "void main(){ vec4 c = texture(police, t);\n"
    "             f = vec4(uCouleur.rgb, c.a * uCouleur.a); }\n";

/* A-Z, 0-9, espace, point, tiret, barre, apostrophe. 5 bits par ligne. */
static const unsigned char osd_glyphes[41][7] = {
    {0x0E,0x11,0x11,0x1F,0x11,0x11,0x11}, {0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E},
    {0x0E,0x11,0x10,0x10,0x10,0x11,0x0E}, {0x1E,0x11,0x11,0x11,0x11,0x11,0x1E},
    {0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F}, {0x1F,0x10,0x10,0x1E,0x10,0x10,0x10},
    {0x0E,0x11,0x10,0x17,0x11,0x11,0x0E}, {0x11,0x11,0x11,0x1F,0x11,0x11,0x11},
    {0x0E,0x04,0x04,0x04,0x04,0x04,0x0E}, {0x07,0x02,0x02,0x02,0x02,0x12,0x0C},
    {0x11,0x12,0x14,0x18,0x14,0x12,0x11}, {0x10,0x10,0x10,0x10,0x10,0x10,0x1F},
    {0x11,0x1B,0x15,0x15,0x11,0x11,0x11}, {0x11,0x19,0x15,0x13,0x11,0x11,0x11},
    {0x0E,0x11,0x11,0x11,0x11,0x11,0x0E}, {0x1E,0x11,0x11,0x1E,0x10,0x10,0x10},
    {0x0E,0x11,0x11,0x11,0x15,0x12,0x0D}, {0x1E,0x11,0x11,0x1E,0x14,0x12,0x11},
    {0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E}, {0x1F,0x04,0x04,0x04,0x04,0x04,0x04},
    {0x11,0x11,0x11,0x11,0x11,0x11,0x0E}, {0x11,0x11,0x11,0x11,0x11,0x0A,0x04},
    {0x11,0x11,0x11,0x15,0x15,0x15,0x0A}, {0x11,0x11,0x0A,0x04,0x0A,0x11,0x11},
    {0x11,0x11,0x0A,0x04,0x04,0x04,0x04}, {0x1F,0x01,0x02,0x04,0x08,0x10,0x1F},
    {0x0E,0x11,0x13,0x15,0x19,0x11,0x0E}, {0x04,0x0C,0x04,0x04,0x04,0x04,0x0E},
    {0x0E,0x11,0x01,0x02,0x04,0x08,0x1F}, {0x1F,0x02,0x04,0x02,0x01,0x11,0x0E},
    {0x02,0x06,0x0A,0x12,0x1F,0x02,0x02}, {0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E},
    {0x06,0x08,0x10,0x1E,0x11,0x11,0x0E}, {0x1F,0x01,0x02,0x04,0x08,0x08,0x08},
    {0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E}, {0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C},
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00}, {0x00,0x00,0x00,0x00,0x00,0x0C,0x0C},
    {0x00,0x00,0x00,0x1F,0x00,0x00,0x00}, {0x01,0x02,0x02,0x04,0x08,0x08,0x10},
    {0x0C,0x04,0x08,0x00,0x00,0x00,0x00},
};

static int osd_index(char c) {
    if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= '0' && c <= '9') return 26 + (c - '0');
    if (c == '.') return 37;
    if (c == '-') return 38;
    if (c == '/') return 39;
    if (c == '\'') return 40;
    return 36;
}

static void osd_init(void) {
    if (g_osd_shader) return;

    unsigned char atlas[OSD_TEXW * OSD_TEXH * 4];
    memset(atlas, 0, sizeof atlas);
    for (int g = 0; g < 42; ++g) {
        int cx = (g % OSD_COLS) * OSD_CW, cy = (g / OSD_COLS) * OSD_CH;
        for (int r = 0; r < 7; ++r) {
            unsigned bits = g < 41 ? osd_glyphes[g][r] : 0x1Fu;
            for (int c = 0; c < 5; ++c) {
                if (!(bits & (1u << (4 - c)))) continue;
                unsigned char *p = atlas + (size_t)(((cy + (7 - r)) * OSD_TEXW) + cx + c) * 4u;
                p[0] = p[1] = p[2] = 255; p[3] = 255;
            }
        }
    }

    GLuint vs = compile_shader(GL_VERTEX_SHADER, osd_vert_src);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, osd_frag_src);
    g_osd_shader = glCreateProgram();
    glAttachShader(g_osd_shader, vs);
    glAttachShader(g_osd_shader, fs);
    glLinkProgram(g_osd_shader);
    glDeleteShader(vs);
    glDeleteShader(fs);
    g_osd_couleur = glGetUniformLocation(g_osd_shader, "uCouleur");
    g_osd_police  = glGetUniformLocation(g_osd_shader, "police");

    glGenVertexArrays(1, &g_osd_vao);
    glGenBuffers(1, &g_osd_vbo);
    glBindVertexArray(g_osd_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_osd_vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(OsdSommet), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(OsdSommet), (void *)(2 * sizeof(float)));
    glBindVertexArray(0);

    glGenTextures(1, &g_osd_tex);
    glBindTexture(GL_TEXTURE_2D, g_osd_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, OSD_TEXW, OSD_TEXH, 0, GL_RGBA, GL_UNSIGNED_BYTE, atlas);
}

static void osd_sommet(OsdSommet *v, int *k, float x, float y, float u, float vv,
                       float px, float py) {
    v[*k].x = -1.0f + x * px;
    v[*k].y = -1.0f + y * py;
    v[*k].u = u;
    v[*k].v = vv;
    ++*k;
}

static void osd_quad(OsdSommet *v, int *k, float x, float y, float w, float h,
                     float u0, float v0, float u1, float v1, float px, float py) {
    osd_sommet(v, k, x,     y,     u0, v0, px, py);
    osd_sommet(v, k, x + w, y,     u1, v0, px, py);
    osd_sommet(v, k, x,     y + h, u0, v1, px, py);
    osd_sommet(v, k, x + w, y,     u1, v0, px, py);
    osd_sommet(v, k, x + w, y + h, u1, v1, px, py);
    osd_sommet(v, k, x,     y + h, u0, v1, px, py);
}

/* Appele une fois par image, juste avant l'echange des tampons. */
void pvr2_osd_frame(void) {
    const char *texte = dc_cinematic_message();
    if (!texte || !*texte) return;
    osd_init();
    if (!g_osd_shader) return;

    int vx = 0, vy = 0, vw = g_width, vh = g_height;
    pvr2_viewport(g_width, g_height, &vx, &vy, &vw, &vh);
    if (vw <= 0 || vh <= 0) return;
    glViewport(vx, vy, vw, vh);

    int n = (int)strlen(texte);
    while (n > 0 && texte[n - 1] == ' ') --n;
    if (n <= 0) return;

    int s = vh >= 900 ? 5 : (vh >= 600 ? 4 : 3);
    int txt_w = n * 6 * s - s;
    int txt_h = 7 * s;
    float px = 2.0f / (float)vw, py = 2.0f / (float)vh;
    float x0 = (float)((vw - txt_w) / 2);
    float y0 = (float)(3 * s);

    OsdSommet *v = (OsdSommet *)malloc(sizeof(OsdSommet) * 6u * (size_t)(n * 35 + 1));
    if (!v) return;

    glDisable(GL_DEPTH_TEST);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glUseProgram(g_osd_shader);
    glUniform1i(g_osd_police, 0);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, g_osd_tex);
    glBindVertexArray(g_osd_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_osd_vbo);

    int k = 0;
    float tu = (float)((41 % OSD_COLS) * OSD_CW + 2) / (float)OSD_TEXW;
    float tv = (float)((41 / OSD_COLS) * OSD_CH + 4) / (float)OSD_TEXH;
    osd_quad(v, &k, 0.0f, 0.0f, (float)vw, (float)(txt_h + 5 * s), tu, tv, tu, tv, px, py);
    glUniform4f(g_osd_couleur, 0.0f, 0.0f, 0.0f, 0.55f);
    glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)(k * (int)sizeof(OsdSommet)), v, GL_STREAM_DRAW);
    glDrawArrays(GL_TRIANGLES, 0, k);

    k = 0;
    for (int i = 0; i < n; ++i) {
        int g = osd_index(texte[i]);
        int cx = (g % OSD_COLS) * OSD_CW, cy = (g / OSD_COLS) * OSD_CH;
        float gx = x0 + (float)(i * 6 * s);
        for (int r = 0; r < 7; ++r) {
            unsigned bits = osd_glyphes[g][r];
            if (!bits) continue;
            for (int c = 0; c < 5; ++c) {
                if (!(bits & (1u << (4 - c)))) continue;
                float u0 = (float)(cx + c) / (float)OSD_TEXW;
                float u1 = (float)(cx + c + 1) / (float)OSD_TEXW;
                float v0 = (float)(cy + (7 - r)) / (float)OSD_TEXH;
                float v1 = (float)(cy + (7 - r) + 1) / (float)OSD_TEXH;
                osd_quad(v, &k, gx + (float)(c * s), y0 + (float)((6 - r) * s),
                         (float)s, (float)s, u0, v0, u1, v1, px, py);
            }
        }
    }
    glUniform4f(g_osd_couleur, 1.0f, 1.0f, 0.80f, 1.0f);
    glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)(k * (int)sizeof(OsdSommet)), v, GL_STREAM_DRAW);
    glDrawArrays(GL_TRIANGLES, 0, k);

    free(v);
    glDisable(GL_BLEND);
    glBindVertexArray(0);
    glUseProgram(0);
    glEnable(GL_DEPTH_TEST);
}

void pvr2_render_frame(void) {
    static unsigned dbg_frame=0; static int dbg_at=-1;
    if(dbg_at<0) { const char *x=getenv("DCRECOMP_TRACE_GEOM_FRAME"); dbg_at=x?atoi(x):0; }
    ++dbg_frame;
    glBindFramebuffer(GL_FRAMEBUFFER,g_render_fbo);
    glViewport(0,0,g_render_width,g_render_height);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

    glUseProgram(g_shader);
    glUniform2f(g_uniform_screen_size, 640.0f, 480.0f);
    { static int show = -1;
      if (show < 0) show = getenv("PHANTOM_DC_UV_DEBUG") != NULL;
      glUniform1i(g_uniform_show_uv, show); }
    glEnable(GL_DEPTH_TEST);

    glBindVertexArray(g_vao);
    /* Bind our own buffer before uploading into it. A vertex array object
     * remembers which buffer each attribute came from, but GL_ARRAY_BUFFER
     * itself is global state - and the framebuffer present binds its own quad
     * buffer there. Without this, the geometry upload lands in that quad, the
     * draw reads attributes from a buffer that still holds twelve floats, and
     * the driver walks off the end of it. */
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);

    /* Draw lists in order: opaque → punch-through → translucent */
    ++g_texture_frame;   /* commence à 1 : seen == 0 veut dire « jamais servie » */
    uint32_t palette[1024]; DCHardware *hw = sh4_get_hardware();
    unsigned palette_format = dc_hw_read32(hw, 0x005f8108u) & 3u;
    pvr2_texture_set_stride((dc_hw_read32(hw, 0x005f80e4u) & 31u) * 32u);   /* TEXT_CONTROL */
    for (unsigned i=0; i<1024; ++i) palette[i]=dc_hw_read32(hw, PVR_PALETTE_BASE+i*4u);
    int draw_order[] = {
        PVR2_LIST_OPAQUE,
        PVR2_LIST_PUNCHTHRU,
        PVR2_LIST_TRANS
    };

    for (int i = 0; i < 3; i++) {
        const PVR2TriList *list = pvr2_ta_get_list(draw_order[i]);
        if (!list || list->count == 0) continue;
        const PVR2RenderVertex *vertices = draw_order[i] == PVR2_LIST_TRANS ? sort_translucent(list) : list->vertices;
        glUniform1i(g_uniform_punch,draw_order[i] == PVR2_LIST_PUNCHTHRU);
        glUniform1f(g_uniform_punch_ref,(float)(dc_hw_read32(hw,0x005f811cu)&0xffu));

        /* Upload vertex data */
        glBufferData(GL_ARRAY_BUFFER,
                     (GLsizeiptr)(list->count * sizeof(PVR2RenderVertex)),
                     vertices, GL_STREAM_DRAW);

        /* Blending for translucent list */
        if (draw_order[i] == PVR2_LIST_TRANS) {
            glEnable(GL_BLEND);
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
            glDepthMask(GL_FALSE);
        } else {
            glDisable(GL_BLEND);
            glDepthMask(GL_TRUE);
        }

        /* TA vertices carry the texture settings active when emitted. Preserve
         * their order, splitting only where those settings change. */
        for (int cursor = 0; cursor < list->count;) {
            int first = cursor, end = cursor+1;
            const PVR2RenderVertex *v = &vertices[first];
            while (end < list->count && vertices[end].pcw == v->pcw &&
                   vertices[end].isp == v->isp && vertices[end].tsp == v->tsp && vertices[end].tcw == v->tcw) end++;
            int w = 0, h = 0, textured = 0;
            GLuint texture = 0;
            if ((v->pcw & 8u) && g_texture_pixels) {
                int k = texture_find(v->tsp, v->tcw);
                if (k < 0) k = texture_new(v->tsp, v->tcw);
                uint64_t print = 0;
                int fresh = g_textures[k].seen != g_texture_frame;
                if (fresh) dc_ralentis_debut(RAL_TEXTURES);
                if (fresh) print = pvr2_texture_fingerprint(sh4_get_vram_ptr(), v->tsp, v->tcw, palette, palette_format);
                if (fresh && (!g_textures[k].seen || print != g_textures[k].print)) {
                    g_textures[k].print = print;
                    g_textures[k].valid=pvr2_decode_texture(sh4_get_vram_ptr(), v->tsp, v->tcw,
                                           g_texture_pixels, 1024u * 1024u * 4u, &w, &h,
                                           palette, palette_format);
                    g_textures[k].w = w; g_textures[k].h = h;
                    if (g_textures[k].valid) {
                        { static int dossier = -2;
                          if (dossier == -2) dossier = getenv("PHANTOM_DC_TEXDUMP") != NULL;
                          if (dossier) {
                              char chemin[512];
                              snprintf(chemin, sizeof chemin, "%s/tex_%08X_%08X.ppm",
                                       getenv("PHANTOM_DC_TEXDUMP"), v->tsp, v->tcw);
                              FILE *t = fopen(chemin, "wb");
                              if (t) {
                                  fprintf(t, "P6\n%d %d\n255\n", w, h);
                                  for (int p = 0; p < w * h; ++p)
                                      fwrite(&g_texture_pixels[p * 4], 1, 3, t);
                                  fclose(t);
                              }
                          } }
                        if (!g_textures[k].id) glGenTextures(1,&g_textures[k].id);
                        glActiveTexture(GL_TEXTURE0);
                        glBindTexture(GL_TEXTURE_2D,g_textures[k].id);
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
                        /* Toujours répéter : le blocage et le miroir dépendent
                         * de la primitive, pas de la texture, et se font donc
                         * dans le nuanceur (adresse_texture). */
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
                        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,g_texture_pixels);
                    }
                }
                if (fresh) { g_textures[k].seen = g_texture_frame; dc_ralentis_fin(RAL_TEXTURES); }
                w = g_textures[k].w; h = g_textures[k].h;
                textured = g_textures[k].valid; texture = g_textures[k].id;
            }
            { static int on = -1; static float zx0=0,zy0=0,zx1=0,zy1=0; static int parsed; static float aire_min=-1;
              if (on < 0) { on = getenv("DCRECOMP_TRACE_ZONE") != NULL;
                  const char *z = getenv("DCRECOMP_TRACE_ZONE");
                  const char *a = getenv("DCRECOMP_TRACE_MIN_AREA");
                  aire_min = a ? (float)atof(a) : 0.0f;
                  if (on && z) parsed = sscanf(z, "%f,%f,%f,%f", &zx0,&zy0,&zx1,&zy1); }
              if (on && parsed == 4) {
                  for (int n=first; n+2<end; n+=3) {
                      const PVR2RenderVertex *a=&vertices[n], *b=&vertices[n+1], *c=&vertices[n+2];
                      float x0=a->x,y0=a->y,x1=a->x,y1=a->y, u0=a->u,u1=a->u,v0=a->v,v1=a->v;
                      if(b->x<x0)x0=b->x; if(c->x<x0)x0=c->x; if(b->x>x1)x1=b->x; if(c->x>x1)x1=c->x;
                      if(b->y<y0)y0=b->y; if(c->y<y0)y0=c->y; if(b->y>y1)y1=b->y; if(c->y>y1)y1=c->y;
                      if(b->u<u0)u0=b->u; if(c->u<u0)u0=c->u; if(b->u>u1)u1=b->u; if(c->u>u1)u1=c->u;
                      if(b->v<v0)v0=b->v; if(c->v<v0)v0=c->v; if(b->v>v1)v1=b->v; if(c->v>v1)v1=c->v;
                      float cx=(x0+x1)*.5f, cy=(y0+y1)*.5f;
                      int in = cx>=zx0 && cx<=zx1 && ((cy>=zy0 && cy<=zy1) || (479.0f-cy>=zy0 && 479.0f-cy<=zy1));
                      if (in && (x1-x0)>=5.0f && (y1-y0)>=3.0f && (x1-x0)*(y1-y0)>=aire_min)
                          pvr2_trace_zone_line(dbg_frame, draw_order[i], textured, w, h, v->pcw, v->tsp, v->tcw,
                                               x0,y0,x1,y1,u0,u1,v0,v1,
                                               a->u,a->v,b->u,b->v,c->u,c->v,
                                               a->x,a->y,b->x,b->y,c->x,c->y);
                  }
              } }
            { static int on = -1; static unsigned printed;
              if (on < 0) on = getenv("DCRECOMP_TRACE_UNTEXTURED_TRI") != NULL;
              if (on && !(v->pcw & 8u)) {
                  static unsigned lines;
                  for (int n=first; n+2<end && lines<400000; n+=3) {
                      const PVR2RenderVertex *a=&vertices[n], *b=&vertices[n+1], *c=&vertices[n+2];
                      float x0=a->x,y0=a->y,x1=a->x,y1=a->y;
                      if(b->x<x0)x0=b->x; if(c->x<x0)x0=c->x; if(b->x>x1)x1=b->x; if(c->x>x1)x1=c->x;
                      if(b->y<y0)y0=b->y; if(c->y<y0)y0=c->y; if(b->y>y1)y1=b->y; if(c->y>y1)y1=c->y;
                      if ((x1-x0) >= 12.0f && (y1-y0) >= 6.0f && (x1-x0)*(y1-y0) >= 250.0f) {
                          lines++;
                          printf("[TRI-NOTEX] frame=%u list=%d pcw=%08X tsp=%08X tcw=%08X box=%.0f,%.0f..%.0f,%.0f color=%.2f,%.2f,%.2f,%.2f\n",
                                 dbg_frame, draw_order[i], v->pcw, v->tsp, v->tcw, x0,y0,x1,y1, a->r,a->g,a->b,a->a);
                      }
                  }
              } }
            { static int on = -1; static unsigned printed;
              if (on < 0) on = getenv("DCRECOMP_TRACE_TEXTURES") != NULL;
              if (on && (v->pcw & 8u) && !textured && printed < 4000) {
                  printed++;
                  float x0=1e9f,x1=-1e9f,y0=1e9f,y1=-1e9f;
                  for (int n=first;n<end;++n) { const PVR2RenderVertex *q=&vertices[n];
                      if(q->x<x0)x0=q->x; if(q->x>x1)x1=q->x; if(q->y<y0)y0=q->y; if(q->y>y1)y1=q->y; }
                  printf("[TEX-KO] frame=%u list=%d n=%d pcw=%08X tsp=%08X tcw=%08X fmt=%u lin=%u str=%u vq=%u mip=%u uv=%.2f,%.2f box=%.0f,%.0f..%.0f,%.0f\n",
                         dbg_frame, draw_order[i], end-first, v->pcw, v->tsp, v->tcw,
                         (v->tcw>>27)&7u, (v->tcw>>26)&1u, (v->tcw>>25)&1u, (v->tcw>>30)&1u, v->tcw>>31,
                         v->u, v->v, x0, y0, x1, y1);
              } }
            { static int on = -1; static uint32_t last_tsp, last_tcw; static int n;
              if (on < 0) on = getenv("PHANTOM_DC_YUV_TRACE") != NULL;
              if (on && (v->pcw & 8u) && ((v->tcw >> 27) & 7u) == 3u && n < 300) {
                  n++; last_tsp = v->tsp; last_tcw = v->tcw;
                  printf("[YUV-POLY] frame %u tsp=%08X tcw=%08X tex=%d %dx%d pcw=%08X count=%d:", dbg_frame,
                         v->tsp, v->tcw, textured, w, h, v->pcw, end - first);
                  for (int q = first; q < end && q < first + 6; q++)
                      printf(" (%.0f,%.0f uv %.3f,%.3f)", vertices[q].x, vertices[q].y, vertices[q].u, vertices[q].v);
                  printf("\n");
              } }
            { static int from = -1, to;
              if (from < 0) { const char *a = getenv("DCRECOMP_TRACE_GEOM_FROM"), *b = getenv("DCRECOMP_TRACE_GEOM_TO");
                              from = a ? atoi(a) : 0; to = b ? atoi(b) : 0; }
              if (from && dbg_frame >= (unsigned)from && dbg_frame <= (unsigned)to)
                  printf("[GEOM] frame %u list=%d count=%d pcw=%08X tsp=%08X tcw=%08X tex=%d %dx%d xy=%.0f,%.0f uv=%.3f,%.3f\n",
                         dbg_frame, draw_order[i], end - first, v->pcw, v->tsp, v->tcw, textured, w, h, v->x, v->y, v->u, v->v); }
            if (dbg_at && dbg_frame == (unsigned)dbg_at)
                printf("[GEOM] list=%d count=%d pcw=%08X isp=%08X tsp=%08X tcw=%08X tex=%d xyz=%.1f,%.1f,%.8g uv=%.3f,%.3f rgba=%.2f,%.2f,%.2f,%.2f\n", draw_order[i],end-first,v->pcw,v->isp,v->tsp,v->tcw,textured,v->x,v->y,v->z,v->u,v->v,v->r,v->g,v->b,v->a);
            static const GLenum comparisons[8] = { GL_NEVER, GL_LESS, GL_EQUAL, GL_LEQUAL,
                                                    GL_GREATER, GL_NOTEQUAL, GL_GEQUAL, GL_ALWAYS };
            glDepthFunc(draw_order[i] == PVR2_LIST_PUNCHTHRU || draw_order[i] == PVR2_LIST_TRANS ? GL_GEQUAL : comparisons[(v->isp >> 29) & 7u]);
            glDepthMask(draw_order[i] == PVR2_LIST_TRANS ? GL_FALSE :
                        draw_order[i] == PVR2_LIST_PUNCHTHRU ? GL_TRUE :
                        (v->isp & (1u << 26)) ? GL_FALSE : GL_TRUE);
            glUniform1i(g_uniform_textured, textured);
            glUniform1i(g_uniform_shading, (v->tsp >> 6) & 3);
            glUniform1i(g_uniform_alpha, (v->tsp >> 20) & 1);
            glUniform1i(g_uniform_ignore_tex_alpha, (v->tsp >> 19) & 1);
            if (draw_order[i] == PVR2_LIST_TRANS) {
                static const GLenum src[8]={GL_ZERO,GL_ONE,GL_DST_COLOR,GL_ONE_MINUS_DST_COLOR,GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA};
                static const GLenum dst[8]={GL_ZERO,GL_ONE,GL_SRC_COLOR,GL_ONE_MINUS_SRC_COLOR,GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA};
                glBlendFunc(src[(v->tsp>>29)&7], dst[(v->tsp>>26)&7]);
            }
            if (textured) {
                glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_2D,texture);
                int clamp_u=(v->tsp & (1u<<16))!=0,clamp_v=(v->tsp & (1u<<15))!=0;
                int flip_u=(v->tsp & (1u<<18))!=0,flip_v=(v->tsp & (1u<<17))!=0;
                /* A full-image sprite tile should not sample the opposite
                 * edge when bilinear filtering touches exactly 0 or 1. */
                if (((v->pcw>>29)&7u)==TA_PARAM_SPRITE) {
                    int within_u=1,within_v=1;
                    for (int n=first;n<end;++n) {
                        if (vertices[n].u < -0.00001f || vertices[n].u > 1.00001f) within_u=0;
                        if (vertices[n].v < -0.00001f || vertices[n].v > 1.00001f) within_v=0;
                    }
                    clamp_u |= within_u; clamp_v |= within_v;
                }
                { static int wrap = -2;
                  if (wrap == -2) { const char *e = getenv("PHANTOM_DC_SANS_MIROIR"); wrap = e ? atoi(e) : 0; }
                  if (wrap) { flip_u = flip_v = 0; } }
                glUniform1i(g_uniform_clamp_u,clamp_u);
                glUniform1i(g_uniform_clamp_v,clamp_v);
                glUniform1i(g_uniform_flip_u,flip_u);
                glUniform1i(g_uniform_flip_v,flip_v);
            }
            glDrawArrays(GL_TRIANGLES, first, end - first);
            cursor = end;
        }
    }

    /* Reset state */
    glDepthMask(GL_TRUE);
    glDisable(GL_BLEND);

    glBindVertexArray(0);
    glUseProgram(0);
    int x,y,w,h;
    if (pvr2_viewport(g_width,g_height,&x,&y,&w,&h)) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER,g_render_fbo);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER,0);
        glClearColor(0.0f,0.0f,0.0f,1.0f); glClear(GL_COLOR_BUFFER_BIT);
        glBlitFramebuffer(0,0,g_render_width,g_render_height,x,y,x+w,y+h,GL_COLOR_BUFFER_BIT,GL_LINEAR);
        glClearColor(0.0f,0.0f,0.1f,1.0f);
    }
    glBindFramebuffer(GL_FRAMEBUFFER,0);
}

void pvr2_render_resize(int width, int height) {
    int x,y,w,h;
    if (!pvr2_viewport(width,height,&x,&y,&w,&h)) return;
    g_width=width; g_height=height;
    glViewport(x,y,w,h);
}

#else /* !HAS_OPENGL */

int pvr2_render_init(int w, int h) {
    (void)w; (void)h;
    printf("[PVR2] Renderer disabled (no OpenGL)\n");
    return 0;
}
void pvr2_render_destroy(void) {}
void pvr2_render_frame(void) {}
void pvr2_osd_frame(void) {}
int pvr2_present_framebuffer(uint32_t a, uint32_t c, int w, int h) {
    (void)a; (void)c; (void)w; (void)h; return 0;
}
void pvr2_render_resize(int w, int h) { (void)w; (void)h; }
int pvr2_screenshot(const char *path) { (void)path; return -1; } /* nothing drawn to read back */

#endif /* HAS_OPENGL */
