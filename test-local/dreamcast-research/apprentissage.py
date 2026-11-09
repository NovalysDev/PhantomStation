"""What a Dreamcast disc needs, found by running it rather than written by hand.

The boot program is translated as before. The compiler then runs the result
hidden, presses Start and A, and the engine writes down every call to code it
has no translation for, with a copy of the RAM holding it (pal-prototype/learn.c).
That code is translated in turn and the game built again, until a run finds
nothing new. What was learned is kept per disc, so the next compilation starts
from it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SIGNATURE_BYTES = 32
MAX_BLOCK_TARGETS = 4096

# Interrupt dispatcher of Sega's Katana library, which most Dreamcast games
# link. Register numbers and the call offset vary between titles; checked on
# Crazy Taxi, House of the Dead 2, Grandia II and Soulcalibur (Europe).
_KATANA_IRQ = bytes.fromhex('e62fd62fc62fb62fa62f962f862f224f2edcc26dd36326de08e888230689c7bc09000364482401890b4e0900d36220e4')
_KATANA_MASK = bytes.fromhex('fffffffffffffffffffffffffffffffffff00ff00ffffffffff00fffffff00f0ffffffffffffffffffffffff0fffffff')
_KATANA_PATTERN = bytes(b & m for b, m in zip(_KATANA_IRQ, _KATANA_MASK))


def find_katana_irq(boot: bytes, base: int = 0x8c010000) -> int | None:
    first = _KATANA_PATTERN[:16]
    hits = []
    start = 0
    while True:
        off = boot.find(first, start)
        if off < 0: break
        start = off + 2
        if off & 1: continue
        window = boot[off:off+len(_KATANA_PATTERN)]
        if len(window) == len(_KATANA_PATTERN) and all(w & m == p for w, m, p in zip(window, _KATANA_MASK, _KATANA_PATTERN)):
            hits.append(base + off)
    return hits[0] if len(hits) == 1 else None


def plausible_entry(data: bytes, off: int, from_disc: bool) -> bool:
    """Whether a call target looks like code rather than a stray pointer.

    Calls made with corrupted registers land in data; learning those would
    compile tables and stacks as programs. A program read from the disc is
    trusted unless the target starts with empty words. RAM copied around an
    address with no disc read behind it must start like a function: a
    register push, a return-address push, a stack adjustment or a jump stub.
    """
    ops = [int.from_bytes(data[off+i:off+i+2], 'little') for i in range(0, 16, 2) if off+i+2 <= len(data)]
    if len(ops) < 2 or ops[0] in (0x0000, 0xFFFF) or ops[1] in (0x0000, 0xFFFF): return False
    if from_disc: return True
    if any((o & 0xFF0F) == 0x2F06 or o in (0x4F22, 0x4F12) or (o & 0xFF80) == 0x7F80
           or (o & 0xF0FF) == 0x402B for o in ops[:4]):
        return True
    # A trampoline: a few loads and an RTE or RTS, with no empty word before
    # it. Crazy Taxi's boot stub leaves the game through one it copies to
    # 0x8C00FA20 (four loads, then RTE to the game's entry).
    for o in ops:
        if o in (0x0000, 0xFFFF): return False
        if o in (0x002B, 0x000B): return True
    return False


def _function_start(boot: bytes):
    """Whether an offset of the main program can start a function: it starts
    like one (a push, a stack adjustment, a jump stub), or it sits right after
    the end of another (RTS or a jump that does not come back, nop padding, or
    a literal pool after them) and does not start with empty words.

    Never inside a literal pool: an address the program reads as a constant
    is data. BRA does not end a function here - pools are full of register
    addresses whose upper half reads as one (0xA05F...), and that sent an
    entry into Maple code's pool at 0x8C0D00FC that stopped House's VBlank."""
    def half(o): return int.from_bytes(boot[o:o+2], 'little') if 0 <= o < len(boot) - 1 else None
    pool = set()
    for o in range(0, len(boot) - 1, 2):
        op = half(o)
        if op & 0xF000 == 0xD000:
            la = ((o + 4) & ~3) + (op & 0xFF) * 4; pool.update((la, la + 2))
        elif op & 0xF000 == 0x9000:
            pool.add(o + 4 + (op & 0xFF) * 2)
    def ends(op):   # RTS, or a jump that does not come back: JMP @Rn
        return op is not None and (op == 0x000B or op & 0xF0FF == 0x402B)
    def after_function(o):
        if ends(half(o - 4)) or half(o - 2) == 0x0009: return True
        return not o & 3 and any(ends(half(o - k - 4)) for k in range(4, 64, 4))
    import sys
    tools = str(Path(__file__).resolve().parent / 'dcrecomp' / 'tools')
    if tools not in sys.path: sys.path.insert(0, tools)
    from sh4_disasm import decode_sh4
    def decodes(o):  # four real SH-4 instructions: 0x0100 is none (House's Maple pool)
        return all(half(o + 2 * k) is not None and decode_sh4(half(o + 2 * k), 0)[0] != '.word' for k in range(4))
    return lambda o: o not in pool and decodes(o) and (plausible_entry(boot, o, False)
                                                       or (plausible_entry(boot, o, True) and after_function(o)))


def code_constants(boot: bytes, base: int = 0x8C010000) -> set[int]:
    """Main-program functions the main program itself names in a constant.

    A task, a callback or a state handler is installed by loading its address
    from a literal pool (MOV.L @(disp,PC)) and storing it, never by calling
    it, so no call leads the translator there: House of the Dead 2's task
    functions turned up one per hidden run, the action stopping each time on
    the next one. A loaded constant that points at a function start is one.
    Pareil pour un programme lu sur le disque (base = son adresse) : les tâches
    de ses zombies et de ses scènes s'installent de la même façon - non
    traduites, un zombie restait invisible et la scène suivante ne venait pas."""
    lo = base & 0x1FFFFFFF
    hi = lo + len(boot)
    good = _function_start(boot)
    found = set()
    for o in range(0, len(boot) - 1, 2):
        op = int.from_bytes(boot[o:o+2], 'little')
        if op & 0xF000 != 0xD000: continue
        la = ((o + 4) & ~3) + (op & 0xFF) * 4
        if la + 4 > len(boot): continue
        a = int.from_bytes(boot[la:la+4], 'little') & 0x1FFFFFFF
        if lo <= a < hi and not a & 1 and good(a - lo):
            found.add(a | 0x80000000)
    return found


def table_pointers(data: bytes, base: int) -> set[int]:
    """Fonctions d'un programme lu sur le disque rangées dans ses propres tables.

    Un programme choisit souvent l'étape à faire dans une table d'adresses
    (« étape n -> fonction ») qu'il lit avec un index : aucun appel direct ni
    aucune constante ne mène le traducteur à ces fonctions. Le programme de
    sauvegarde de House of the Dead 2 (0x8C42E800) range ainsi ses 19 étapes
    en 0x8C43A704 ; l'étape « relire une sauvegarde » (0x8C42ED88) n'était
    jamais traduite et le jeu restait sur un écran noir dès qu'une sauvegarde
    existait. Une suite d'au moins deux mots consécutifs pointant dans le
    programme est une table quand la moitié au moins de ses cases tombent sur
    un début de fonction : celles-là se portent garantes des autres."""
    lo = base & 0x1FFFFFFF
    hi = lo + len(data)
    good = _function_start(data)
    words = [int.from_bytes(data[i:i+4], 'little') & 0x1FFFFFFF for i in range(0, len(data) - 3, 4)]
    inside = [lo <= a < hi and not a & 1 for a in words]
    found = set()
    k = 0
    while k < len(words):
        if not inside[k]: k += 1; continue
        j = k
        while j < len(words) and inside[j]: j += 1
        run = words[k:j]
        if len(run) >= 2:
            sures = [a for a in run if good(a - lo)]
            if 2 * len(sures) >= len(run):
                found.update(a | 0x80000000 for a in run if a in sures or plausible_entry(data, a - lo, True))
        k = j
    return found


def block_pointers(blocks: list[dict], boot: bytes) -> set[int]:
    """Main-program functions the programs read from the disc point at.

    A program the game loads calls back into the main one through pointers
    in its data; each of those is an entry the main translation needs, and
    finding them one hidden run at a time took House of the Dead 2 five runs
    for fifteen of them. An aligned word into the main program is taken
    straight away when its target starts like a function, or sits right after
    the end of one (an RTS and its delay slot, nop padding, or a literal pool
    after them): callbacks are often leaf functions with no prologue - House's
    0x8C018B00 starts with a load. Taking any target that was not empty words
    also took pointers into data in the middle of functions, and the
    translation split there broke House before its title screen."""
    lo, hi = 0x0C010000, 0x0C010000 + len(boot)
    good = _function_start(boot)
    found = set()
    for b in blocks:
        if not b.get('disc'): continue
        data = b['data']
        words = [int.from_bytes(data[i:i+4], 'little') & 0x1FFFFFFF for i in range(0, len(data) - 3, 4)]
        inside = [lo <= a < hi and not a & 1 for a in words]
        k = 0
        while k < len(words):
            if not inside[k]: k += 1; continue
            # A run of consecutive words into the main program is a table:
            # one real function in it vouches for the others (House's state
            # table at 0x8C4A91B0 mixes 0x8C018AD0, after a BRA, with 0x8C018B00).
            j = k
            while j < len(words) and inside[j]: j += 1
            run = words[k:j]
            table = j - k >= 2 and any(good(a - lo) for a in run)
            for a in run:
                if good(a - lo) or (table and plausible_entry(boot, a - lo, True)):
                    found.add(a | 0x80000000)
            k = j
    return found


def cache_root(boot_sha1: str) -> Path:
    local = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    return Path(local) / 'PhantomStation' / 'Dreamcast-apprentissage' / boot_sha1


class Learned:
    """Main-program entries and RAM blocks learned for one disc."""

    def __init__(self, root: Path):
        self.root = root
        self.main: set[int] = set()
        self.blocks: list[dict] = []
        state = root / 'profil.json'
        if state.is_file():
            data = json.loads(state.read_text(encoding='utf-8'))
            self.main = {int(x, 16) for x in data.get('principal', [])}
            for b in data.get('blocs', []):
                path = root / b['fichier']
                if path.is_file():
                    data = path.read_bytes(); base = int(b['base'], 16); disc = b.get('source') != 'fenetre'
                    targets = {int(x, 16) for x in b['cibles']}
                    targets = {x for x in targets if plausible_entry(data, x - base, disc)}
                    if targets:
                        self.blocks.append({'base': base, 'data': data, 'file': b['fichier'],
                                            'targets': targets, 'disc': disc})

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        blocks = []
        for b in self.blocks:
            path = self.root / b['file']
            if not path.exists(): path.write_bytes(b['data'])
            blocks.append({'base': f"0x{b['base']:08X}", 'taille': len(b['data']), 'fichier': b['file'],
                           'source': 'disque' if b.get('disc', True) else 'fenetre',
                           'cibles': [f'0x{t:08X}' for t in sorted(b['targets'])]})
        tmp = self.root / 'profil.json.tmp'
        tmp.write_text(json.dumps({'principal': [f'0x{t:08X}' for t in sorted(self.main)], 'blocs': blocks},
                                  indent=1) + '\n', encoding='utf-8')
        os.replace(tmp, self.root / 'profil.json')

    def merge_sessions(self, boot_end: int) -> int:
        """Take in what the game met during normal play (see the launcher)."""
        new = 0
        if not self.root.is_dir(): return 0
        for session in sorted(self.root.glob('partie-*')):
            if not session.is_dir(): continue
            new += self.merge_run(session, boot_end)
            session.rename(session.with_name('fusionnee-' + session.name[len('partie-'):]))
        if new: self.save()
        return new

    def merge_run(self, run_dir: Path, boot_end: int) -> int:
        """Add what one hidden run reported; returns how many things were new."""
        listing = run_dir / 'appels.txt'
        if not listing.is_file(): return 0
        new = 0
        for line in listing.read_text(encoding='ascii', errors='replace').splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == 'principal':
                t = int(parts[1], 16)
                if t not in self.main and 0x8c010000 <= t < boot_end:
                    self.main.add(t); new += 1
            elif len(parts) >= 6 and parts[0] == 'bloc':
                t, base = int(parts[1], 16), int(parts[3], 16)
                data = (run_dir / parts[5]).read_bytes()
                # Exception vectors are entry points by definition.
                disc = len(parts) > 6 and parts[6] in ('disque', 'vecteur')
                if not data: continue
                sig = data[t-base:t-base+SIGNATURE_BYTES]
                owner = None
                for b in self.blocks:
                    off = t - b['base']
                    if 0 <= off < len(b['data']) and b['data'][off:off+len(sig)] == sig:
                        owner = b; break
                # Une fenêtre de mémoire dont les octets sont ceux d'un programme
                # lu sur le disque est ce programme : même confiance. Grandia II
                # revient après un appel en 0x8C392230 (dans son programme
                # 0x8C380000) par le répartiteur ; noté « fenêtre », ce point de
                # retour était écarté et le jeu restait figé après le 1er combat.
                if owner is not None and owner.get('disc'): disc = True
                if not plausible_entry(data, t - base, disc):
                    continue
                if owner is None:
                    key = hashlib.sha1(base.to_bytes(4, 'little') + data).hexdigest()[:12]
                    owner = {'base': base, 'data': data, 'file': f'bloc-{key}.bin', 'targets': set(), 'disc': disc}
                    self.blocks.append(owner)
                if t not in owner['targets'] and len(owner['targets']) < MAX_BLOCK_TARGETS:
                    owner['targets'].add(t); new += 1
        return new


def _key(block: dict) -> str:
    return block['file'][5:-4]


def _write_if_changed(path: Path, text: str):
    if path.is_file() and path.read_text(encoding='utf-8') == text: return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def sync_tree(src: Path, dst: Path):
    """Copy only what changed, so the next build recompiles only that."""
    seen = set()
    for f in src.rglob('*'):
        if not f.is_file(): continue
        rel = f.relative_to(src); seen.add(rel)
        out = dst / rel
        if out.is_file() and out.stat().st_size == f.stat().st_size and out.read_bytes() == f.read_bytes():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
    for f in list(dst.rglob('*')):
        if f.is_file() and f.relative_to(dst) not in seen: f.unlink()


FUNC_DEF = re.compile(r'^void (L[0-9a-f]{12}_func_8C[0-9A-F]{6})\(SH4CPU \*cpu\) \{', re.M)


def translate_block(block: dict, work: Path, recompile, stage: Path, translator: str = '') -> list[tuple[int, str, bytes]]:
    """Translate one learned RAM block into renamed sources inside stage."""
    key = _key(block)
    out = work / 'blocs' / key
    # Un programme lu sur le disque nomme lui-même ses fonctions de tâche dans
    # ses constantes : on les traduit toutes d'un coup (voir code_constants).
    targets = set(block['targets'])
    if block.get('disc'):
        targets |= code_constants(block['data'], block['base'])
        targets |= table_pointers(block['data'], block['base'])
    targets_text = ''.join(f'0x{t:08X}\n' for t in sorted(targets))
    marker = out / 'cibles.txt'
    stamp = f'# {translator}\n' + targets_text
    if not (marker.is_file() and marker.read_text(encoding='ascii') == stamp):
        if out.exists(): shutil.rmtree(out)
        out.mkdir(parents=True)
        (out / 'bloc.bin').write_bytes(block['data'])
        (out / 'entrees.txt').write_text(targets_text, encoding='ascii')
        recompile(out / 'bloc.bin', out / 'src/game', out / 'include/game', block['base'], out / 'entrees.txt')
        marker.write_text(stamp, encoding='ascii')
    header = (out / 'include/game/game_functions.h').read_text(encoding='utf-8')
    defined = set(re.findall(r'\bvoid func_(8C[0-9A-F]{6})\(', header))
    prefix = f'L{key}_'
    rename = re.compile(r'\b(func|impl)_(8C[0-9A-F]{6})\b')
    # Data compiled as code can branch outside RAM; no stub exists for that.
    outside = re.compile(r'\bfunc_((?!8C)[0-9A-F]{8})\(cpu\)')
    def swap(m):
        if m.group(1) == 'impl' or m.group(2) in defined: return prefix + m.group(0)
        return m.group(0)
    decls = ''.join(f'void {prefix}func_{a}(SH4CPU *cpu);\n' for a in sorted(defined))
    _write_if_changed(stage / 'include/game' / f'learned_{key}.h',
                      '#pragma once\n#include "recompiler/sh4_cpu.h"\n' + decls)
    entries = []
    for i, source in enumerate(sorted((out / 'src/game').glob('game_code_*.c'))):
        text = rename.sub(swap, source.read_text(encoding='utf-8'))
        text = outside.sub(lambda m: f'{{ cpu->pc = 0x{m.group(1)}u; sh4_call_indirect(cpu); }}', text)
        text = text.replace('#include "game/game_functions.h"\n',
                            f'#include "game/game_functions.h"\n#include "game/learned_{key}.h"\n', 1)
        _write_if_changed(stage / 'src/game' / f'game_code_L{key}_{i:03d}.c', text)
        for name in FUNC_DEF.findall(text):
            addr = int(name[-8:], 16)
            off = addr - block['base']
            sig = block['data'][off:off+SIGNATURE_BYTES]
            if len(sig) >= 4: entries.append((addr, name, sig))
    return entries


def write_learned_table(stage: Path, entries: list[tuple[int, str, bytes]]):
    entries = sorted(entries, key=lambda e: (e[0], e[1]))
    blob = bytearray(); rows = []
    for addr, name, sig in entries:
        rows.append(f'    {{ 0x{addr:08X}u, {name}, {len(blob)}u, {len(sig)}u }},')
        blob += sig
    blob_text = ','.join(str(b) for b in blob) or '0'
    text = ('/* Generated by the compiler from the code this disc loaded while learning. */\n'
            '#include "recompiler/sh4_cpu.h"\n#include <string.h>\n\n'
            + ''.join(f'void {name}(SH4CPU *cpu);\n' for name in sorted({e[1] for e in entries}))
            + '\ntypedef struct { uint32_t addr; void (*func)(SH4CPU *cpu); uint32_t sig, len; } LearnedEntry;\n'
            f'static const unsigned char learned_bytes[] = {{{blob_text}}};\n'
            'static const LearnedEntry learned_table[] = {\n' + '\n'.join(rows) + '\n    { 0, 0, 0, 0 }\n};\n'
            f'#define LEARNED_COUNT {len(rows)}\n\n'
            '/* Several programs may have been learned at one address: run the one\n'
            ' * whose bytes are in RAM now. */\n'
            'void (*phantom_learned_function(SH4CPU *cpu, uint32_t addr))(SH4CPU *cpu) {\n'
            '    int lo = 0, hi = LEARNED_COUNT;\n'
            '    while (lo < hi) { int mid = (lo + hi) / 2; if (learned_table[mid].addr < addr) lo = mid + 1; else hi = mid; }\n'
            '    uint32_t off = (addr & 0x1fffffffu) - 0x0c000000u;\n'
            '    for (int i = lo; i < LEARNED_COUNT && learned_table[i].addr == addr; i++) {\n'
            '        const LearnedEntry *e = &learned_table[i];\n'
            '        if (off <= cpu->ram_size && e->len <= cpu->ram_size - off &&\n'
            '            !memcmp(cpu->ram + off, learned_bytes + e->sig, e->len)) return e->func;\n'
            '    }\n'
            '    return 0;\n'
            '}\n')
    _write_if_changed(stage / 'src/game/learned_table.c', text)


def patch_dispatch(stage: Path):
    path = stage / 'src/game/dispatch_table.c'
    text = path.read_text(encoding='utf-8')
    if 'phantom_learned_function' in text: return
    hook = '#ifdef DCRECOMP_ABI_CHECK\n    /* SH-4 keeps'
    miss = '    else {\n        /* BIOS/Boot ROM calls and null pointers are a no-op'
    if text.count(hook) != 1 or text.count(miss) != 1:
        raise RuntimeError('Table de répartition Dreamcast inattendue.')
    checked = '        fn(cpu);\n        static int abi_log = 0;\n'
    plain = '    if (fn) { fn(cpu); }\n'
    if text.count(checked) != 1 or text.count(plain) != 1:
        raise RuntimeError('Table de répartition Dreamcast inattendue.')
    text = text.replace('#include <stddef.h>\n', '#include <stddef.h>\n'
                        'void (*phantom_learned_function(SH4CPU *cpu, uint32_t addr))(SH4CPU *cpu);\n'
                        'void phantom_unresolved(SH4CPU *cpu);\n'
                        'void phantom_stack_switched(SH4CPU *cpu, uint32_t sp0, uint32_t ret, int sp_loaded);\n', 1)
    text = text.replace(hook, '    if (!fn) fn = phantom_learned_function(cpu, lookup);\n' + hook, 1)
    # A call that comes back on another stack is a game thread switch.
    text = text.replace(checked, '        uint32_t sp_loads = sh4_sp_loads;\n        fn(cpu);\n'
                        '        if (cpu->r[15] != saved[7] || (sh4_sp_loads != sp_loads && cpu->pr != from))\n'
                        '            phantom_stack_switched(cpu, saved[7], from, sh4_sp_loads != sp_loads);\n'
                        '        static int abi_log = 0;\n', 1)
    text = text.replace(plain, '    if (fn) { uint32_t sp0 = cpu->r[15], ret = cpu->pr, sp_loads = sh4_sp_loads; fn(cpu);\n'
                        '              if (cpu->r[15] != sp0 || (sh4_sp_loads != sp_loads && cpu->pr != ret))\n'
                        '                  phantom_stack_switched(cpu, sp0, ret, sh4_sp_loads != sp_loads); }\n', 1)
    text = text.replace(miss, '    else {\n        phantom_unresolved(cpu);\n' + miss[len('    else {\n'):], 1)
    path.write_text(text, encoding='utf-8', newline='\n')


STUB = re.compile(r'(void func_(8C[0-9A-F]{6})\(SH4CPU \*cpu\) \{\n    /\* Stub:[^\n]*\*/\n)    \(void\)cpu;\n')


def dynamic_stubs(stage: Path):
    """Direct calls to code outside the translation go through the dispatcher,
    where learned code is found and anything still missing is reported."""
    path = stage / 'src/game/game_stubs.c'
    if not path.is_file(): return
    text = path.read_text(encoding='utf-8')
    text = STUB.sub(lambda m: m.group(1) + f'    cpu->pc = 0x{m.group(2)}u; sh4_call_indirect(cpu);\n', text)
    path.write_text(text, encoding='utf-8', newline='\n')


def write_profile(stage: Path, irq: int | None, flags: int):
    _write_if_changed(stage / 'src/game/phantom_profile.c',
                      '/* Generated by the compiler from this disc. */\n#include <stdint.h>\n'
                      f'const uint32_t phantom_profile_irq = 0x{(irq or 0):08X}u;\n'
                      f'const unsigned phantom_profile_flags = {flags}u;\n')


def learning_run(exe: Path, boot: Path, disc: Path, out: Path, seconds: int = 75) -> None:
    """Run the game hidden, with Start and A pressed now and then."""
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    env = dict(os.environ, PHANTOM_DC_LEARN_DIR=str(out), DCRECOMP_TEST_HIDDEN='1', SDL_AUDIODRIVER='dummy',
               DCRECOMP_TEST_NO_CONTROLLERS='1', DCRECOMP_TEST_CONTROLLER_DESCRIPTOR='1',
               DCRECOMP_NO_PRESENT='1', DCRECOMP_TEST_VMU=str(out / 'carte.vmu'),
               DCRECOMP_TEST_START_MS='12000', DCRECOMP_TEST_START_EVERY_MS='3000',
               DCRECOMP_TEST_A_MS='14000', DCRECOMP_TEST_A_EVERY_MS='2500',
               DCRECOMP_TEST_CLOSE_MS=str(seconds * 1000))
    for k in ('PHANTOM_DC_VMU', 'DCRECOMP_TEST_IRQ_ADDR'): env.pop(k, None)
    with (out / 'journal.log').open('wb') as log:
        try:
            subprocess.run([str(exe), str(boot), str(disc)], cwd=exe.parent, env=env, stdout=log,
                           stderr=subprocess.STDOUT, timeout=seconds + 20,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            pass
