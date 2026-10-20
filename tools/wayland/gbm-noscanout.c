/* LD_PRELOAD shim for experiments: drops GBM_BO_USE_SCANOUT from every gbm buffer allocation, so Mesa is free to pick a
 * tiled layout (UIF on the Pi 4's V3D) instead of linear. Chromium's viz BufferQueue always asks for SHARED_IMAGE_USAGE_SCANOUT,
 * which on V3D means linear buffers. Build: aarch64-linux-gnu-gcc -shared -fPIC -O2 -o gbm-noscanout.so gbm-noscanout.c -ldl */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define SCANOUT (1u << 0)
struct gbm_device;
struct gbm_bo;
static void note(const char *what, uint32_t f) {
    static int n;
    if (n++ < 40) fprintf(stderr, "[gbm-noscanout] %s flags=0x%x -> 0x%x (env %s)\n", what, f, f & ~SCANOUT, getenv("UNITOTEM_NO_SCANOUT_BUFFERS") ? "set" : "unset");
}
struct gbm_bo *gbm_bo_create(struct gbm_device *g, uint32_t w, uint32_t h, uint32_t fmt, uint32_t flags) {
    static struct gbm_bo *(*real)(struct gbm_device *, uint32_t, uint32_t, uint32_t, uint32_t);
    if (!real) real = dlsym(RTLD_NEXT, "gbm_bo_create");
    note("create", flags);
    return real(g, w, h, fmt, flags & ~SCANOUT);
}
struct gbm_bo *gbm_bo_create_with_modifiers2(struct gbm_device *g, uint32_t w, uint32_t h, uint32_t fmt,
                                             const uint64_t *mods, unsigned count, uint32_t flags) {
    static struct gbm_bo *(*real)(struct gbm_device *, uint32_t, uint32_t, uint32_t, const uint64_t *, unsigned, uint32_t);
    if (!real) real = dlsym(RTLD_NEXT, "gbm_bo_create_with_modifiers2");
    note("create_with_modifiers2", flags);
    return real(g, w, h, fmt, mods, count, flags & ~SCANOUT);
}
