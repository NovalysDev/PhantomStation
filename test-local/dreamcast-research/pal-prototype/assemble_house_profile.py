"""Link this verified game's main program with two disk-loaded programs.

All generated inputs are obtained from the selected user-owned disc at build
time. Nothing game-derived is shipped as part of the compiler.
"""
from pathlib import Path
import os
import re
import shutil

root = Path(os.environ['PHANTOM_HOUSE_PROFILE_ROOT'])
dst = root / 'game'
src = dst / 'src/game'
header = dst / 'include/game/game_functions.h'
dispatch = src / 'dispatch_table.c'
first = root / 'overlay1/src/game'
second = root / 'overlay2'
table_re = re.compile(r'^\s*\{ (0x8C[0-9A-F]{6}u), (func_8C[0-9A-F]{6}) \},$', re.M)

dt = '#include <stdlib.h>\n' + dispatch.read_text(encoding='utf8')
base_entries = table_re.findall(dt)
first_entries = [(a, n) for a, n in table_re.findall((first/'dispatch_table.c').read_text())
                 if 0x8C42E800 <= int(a[:-1], 16) < 0x8C43B800]
if not first_entries or not base_entries:
    raise RuntimeError('Table du premier programme chargée depuis le disque introuvable.')
first_sources = sorted(first.glob('game_code_*.c'))
if not first_sources or len(first_sources) > 9:
    raise RuntimeError('Nombre de fichiers inattendu pour le premier programme.')
for index, source in enumerate(first_sources):
    shutil.copy2(source, src/f'game_code_{100+index}.c')
merged = sorted(base_entries + first_entries, key=lambda e: int(e[0][:-1], 16))
if len({a for a, _ in merged}) != len(merged):
    raise RuntimeError('Deux programmes occupent déjà la même entrée.')
start = dt.index('static const FuncEntry func_table[] = {')
end = dt.index('\n};', start) + 3
dt = dt[:start] + 'static const FuncEntry func_table[] = {\n' + '\n'.join(
    f'    {{ {a}, {n} }},' for a, n in merged) + '\n};' + dt[end:]
dt = re.sub(r'#define FUNC_TABLE_SIZE \d+', f'#define FUNC_TABLE_SIZE {len(merged)}', dt, count=1)

header2 = (second/'include/game/game_functions.h').read_text()
names2 = set(re.findall(r'\bvoid (func_8C[0-9A-F]{6})\(SH4CPU \*cpu\);', header2))
entries2 = table_re.findall((second/'src/game/dispatch_table.c').read_text())
if len(entries2) != len(names2):
    raise RuntimeError('Table du deuxième programme incomplète.')
pattern = re.compile(r'\bfunc_8C[0-9A-F]{6}\b')
for index, source in enumerate(sorted((second/'src/game').glob('game_code_*.c'))):
    body = pattern.sub(lambda m: 'house2_'+m.group() if m.group() in names2 else m.group(),
                       source.read_text(encoding='utf8'))
    (src/f'game_code_{110+index}.c').write_text(body, encoding='utf8', newline='\n')

declarations = ['#include <setjmp.h>', 'extern jmp_buf house_jmp;', 'extern int house_jmp_active;']
declarations += [f'void {name}(SH4CPU *cpu);' for _, name in first_entries]
declarations += [f'void house2_{name}(SH4CPU *cpu);' for name in sorted(names2)]
header.write_text(header.read_text(encoding='utf8')+'\n'+'\n'.join(declarations)+'\n', encoding='utf8')
(src/'game_code_199.c').write_text('#include <setjmp.h>\njmp_buf house_jmp;\nint house_jmp_active;\n')

table2 = '\n'.join(f'    {{ {a}, house2_{n} }},' for a, n in entries2)
lookup = '''
extern int dc_test_house_overlay(void);
static const FuncEntry house2_table[] = {
%s
};
static void (*house2_find(uint32_t address))(SH4CPU *) {
    size_t low=0, high=sizeof(house2_table)/sizeof(house2_table[0]);
    while (low < high) {
        size_t mid=low+(high-low)/2;
        if (house2_table[mid].addr < address) low=mid+1; else high=mid;
    }
    return low < sizeof(house2_table)/sizeof(house2_table[0]) && house2_table[low].addr == address
        ? house2_table[low].func : NULL;
}
''' % table2
marker = 'void sh4_call_indirect(SH4CPU *cpu) {'
needle = '    void (*fn)(SH4CPU *cpu) = find_function(lookup);'
assert dt.count(marker) == dt.count(needle) == 1
dt = dt.replace(marker, lookup+'\n'+marker)
dt = dt.replace(needle, '''    void (*fn)(SH4CPU *cpu);
    if (getenv("DCRECOMP_TEST_HOUSE_OVERLAY2") && dc_test_house_overlay() == 2 &&
        lookup >= 0x8c42e800u && lookup < 0x8c4ad000u)
        fn = house2_find(lookup);
    else
        fn = find_function(lookup);''')
dispatch.write_text(dt, encoding='utf8', newline='\n')

old = ('    cpu->pr = 0x8C03C422u; cpu->pc = cpu->r[2]; /* jsr @r2 */\n'
       '    cpu->r[4] = cpu->r[11]; /* delay slot */\n    sh4_call_indirect(cpu);')
new = old.replace('    sh4_call_indirect(cpu);',
                  '    if (getenv("DCRECOMP_TEST_NATIVE_JMP")) { house_jmp_active=1; if (setjmp(house_jmp)==0) sh4_call_indirect(cpu); } else sh4_call_indirect(cpu);')
matches = [f for f in src.glob('game_code_*.c') if old in f.read_text(encoding='utf8')]
assert len(matches)==1
task = matches[0]; text = task.read_text(encoding='utf8')
task.write_text('#include <stdlib.h>\n'+text.replace(old,new), encoding='utf8', newline='\n')
old = ('    cpu->r[0] = 0x0C4CAD88u; /* @0x8C046C8C */\n'
       '    cpu->r[0] = sh4_read32(cpu, cpu->r[0]); /* delay slot */\n    return; /* rts */')
new = old.replace('    return; /* rts */',
                  '    if (house_jmp_active && (cpu->pr & 0x1fffffffu) == 0x0C03C422u && getenv("DCRECOMP_TEST_NATIVE_JMP")) longjmp(house_jmp,1);\n    return; /* rts */')
matches = [f for f in src.glob('game_code_*.c') if old in f.read_text(encoding='utf8')]
assert len(matches)==1
task = matches[0]; text = task.read_text(encoding='utf8')
task.write_text('#include <stdlib.h>\n'+text.replace(old,new), encoding='utf8', newline='\n')
print('Programmes chargés depuis le disque traduits :', len(merged), 'et', len(entries2), 'entrées.')
