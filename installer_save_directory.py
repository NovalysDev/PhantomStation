"""Use PhantomStation's Documents folder for default game saves."""
from pathlib import Path

MARKER = 'PHANTOM_DOCUMENT_SAVES_V1'
ANCHOR = '''    /* Apply writable-state isolation before game-options and launcher setup,'''
HOOK = r'''
    // PHANTOM_DOCUMENT_SAVES_V1: redirect only the original default location.
#ifdef _WIN32
    if (!cli_memcard_dir) {
        const wchar_t* phantom_save_folder = _wgetenv(L"PHANTOM_SAVES_DIR");
        if (phantom_save_folder && *phantom_save_folder) {
            const auto phantom_exe_dir = exe_dir_from_argv(argv[0]).lexically_normal();
            const auto phantom_old_saves = (phantom_exe_dir / "saves").lexically_normal();
            const auto phantom_old_dir = memcard_dir.empty() ? phantom_exe_dir :
                anchor_on_exe_dir(argv[0], memcard_dir).lexically_normal();
            if (phantom_old_dir == phantom_old_saves || phantom_old_dir == phantom_exe_dir) {
                if (!memcard1_path.empty() && memcard1_path.lexically_normal() == phantom_old_dir / "card1.mcd")
                    memcard1_path.clear();
                if (!memcard2_path.empty() && memcard2_path.lexically_normal() == phantom_old_dir / "card2.mcd")
                    memcard2_path.clear();
                memcard_dir = std::filesystem::path(phantom_save_folder);
            }
        }
    }
#endif

'''


def install_save_directory(project):
    path = Path(project) / 'psxrecomp' / 'runtime' / 'src' / 'main.cpp'
    source = path.read_text(encoding='utf-8')
    if MARKER in source:
        return
    if source.count(ANCHOR) != 1:
        raise ValueError('Impossible de relier le dossier des sauvegardes à ce moteur.')
    path.write_text(source.replace(ANCHOR, HOOK + ANCHOR, 1), encoding='utf-8', newline='\n')
