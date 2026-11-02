"""Charge les profils SDL des manettes avant l'ouverture du lanceur et du jeu."""
from pathlib import Path

HELPER = r'''
// PHANTOM_CONTROLLER_DB_V1: mappings supplementaires, sans pilote Windows installe.
static void phantom_load_controller_database(const char* argv0) {
    static bool loaded = false;
    if (loaded) return;
    loaded = true;
    const auto file = exe_dir_from_argv(argv0) / "assets" / "gamecontrollerdb.txt";
    std::error_code ec;
    if (!std::filesystem::is_regular_file(file, ec)) return;
#if defined(PSX_SDL3)
    const int count = SDL_AddGamepadMappingsFromFile(file.string().c_str());
#else
    const int count = SDL_GameControllerAddMappingsFromFile(file.string().c_str());
#endif
    if (count >= 0) std::fprintf(stderr, "PhantomStation: %d profils de manettes charges.\n", count);
    else std::fprintf(stderr, "PhantomStation: base de manettes non chargee: %s\n", SDL_GetError());
}
'''


def install_controller_database(project):
    main = Path(project) / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if 'PHANTOM_CONTROLLER_DB_V1' in source:
        return
    entry = 'int main(int argc, char** argv) {'
    launcher = '            launcher_boot_timing_mark("host:after_sdl_init");'
    game = '    load_input_config(argv[0]);'
    if any(source.count(anchor) != 1 for anchor in (entry, launcher, game)):
        raise ValueError('Initialisation des manettes introuvable.')
    source = source.replace(entry, HELPER + '\n' + entry, 1)
    source = source.replace(launcher, launcher + '\n            phantom_load_controller_database(argv[0]);', 1)
    source = source.replace(game, '    phantom_load_controller_database(argv[0]);\n' + game, 1)
    main.write_text(source, encoding='utf-8', newline='\n')
