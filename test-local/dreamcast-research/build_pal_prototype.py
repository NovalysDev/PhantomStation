"""Translate a Dreamcast CHD using a verified or experimental native profile.

Experimental profiles can build without being playable. Inputs are read-only;
generated code and executables contain game-derived material.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
TOOLS = ROOT / 'toolchain/bin'
PROTOTYPE = LAB / 'pal-prototype'
PROFILE_SHA1 = '2756d6c828b5a3848189cd84cac62bc9cb0a94b7'
HOUSE_BOOT_SHA1 = '339382bcdb9aa9db5484e0e963b0f086bda839b6'
GRANDIA_BOOT_SHA1 = '0cb24122c167a4f199a8aa4408dad4eeb604a0f3'
GRANDIA_OVERLAYS = ((50986, 2, 0x8c300000, '744be314fd309e8845c5ecea6049122926a670d3'),
                    (51251, 30, 0x8c380000, '988329f46c0a2293282896c2b6e572729c655288'))
HOUSE_OVERLAYS = ((254523, 26, 'c68485b4db060da428a780e796d4d747f61ff436'),
                  (254270, 253, '2b6ced4dfa776550b922441ea10f89480bb71f1b'))
LIBCHDR = LAB / 'libchdr-build/libchdr.dll'
SDL_DLL = ROOT / 'n64-engine/generique/SDL2/bin/SDL2.dll'
sys.path.insert(0,str(ROOT/'outils')); sys.path.insert(0,str(LAB))
from dreamcast_probe import ChdError, DreamcastChd, LibChdr, identify_with_boot, read_extent


def select_profile(info: dict, boot: bytes) -> str:
    digest=hashlib.sha1(boot).hexdigest()
    if digest != info.get('boot_sha1'):
        raise ValueError('La vérification du programme du disque a échoué.')
    # Crazy Taxi goes through the learning compiler since 04/11/2026: it plays
    # at least as well as the hand-written profile (crazy_taxi_pal, main.c),
    # textures included, which only PHANTOM_DC_RESEARCH_CRAZY_TAXI=1 still selects.
    if digest == PROFILE_SHA1 and os.getenv('PHANTOM_DC_RESEARCH_CRAZY_TAXI') == '1': return 'crazy_taxi_pal'
    # House of the Dead 2 too since 05/11/2026: learned, it plays past the
    # opening into the game, where its hand-written profile stopped at the car.
    if digest == HOUSE_BOOT_SHA1 and os.getenv('PHANTOM_DC_RESEARCH_HOUSE') == '1': return 'house_pal_experimental'
    if digest == GRANDIA_BOOT_SHA1 and os.getenv('PHANTOM_DC_RESEARCH_GRANDIA') == '1': return 'grandia_pal_experimental'
    return 'generic_experimental'


def pointer_entries(payloads, start, end):
    """Find possible SH-4 code pointers in the programs belonging to a disc."""
    entries=set()
    for data in payloads:
        for i in range(0, len(data)-3, 2):
            addr=int.from_bytes(data[i:i+4], 'little') & 0x1fffffff
            if start <= addr < end and not addr & 1:
                entries.add(addr | 0x80000000)
    return entries


def save_entries(path, entries):
    path.write_text(''.join(f'0x{entry:08X}\n' for entry in sorted(entries)), encoding='ascii')


def run_command(cmd, *, env, timeout=900, on_line=None):
    if on_line is not None:
        process=subprocess.Popen([str(x) for x in cmd],env=env,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        expired=threading.Event()
        def stop():
            expired.set()
            try: process.kill()
            except OSError: pass
        timer=threading.Timer(timeout,stop); timer.daemon=True; timer.start()
        lines=[]
        try:
            for line in process.stdout:
                lines.append(line)
                if len(lines)>200: del lines[0]
                on_line(line.rstrip())
            status=process.wait()
        finally:
            timer.cancel();process.stdout.close()
        if expired.is_set(): raise RuntimeError('Délai dépassé : '+str(cmd[0]))
        if status: raise RuntimeError('Commande échouée : '+str(cmd[0])+'\n'+''.join(lines)[-6000:])
        return ''.join(lines)
    result=subprocess.run([str(x) for x in cmd],env=env,stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                          timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError('Commande échouée : ' + str(cmd[0]) + '\n' + result.stdout[-6000:])
    return result.stdout


LEARNING_ROUNDS = 5


def learning_rounds() -> int:
    """How many times the game is run to teach the compiler, at least once.

    Every disc is learned, with or without a hand-written profile: a build
    that runs the game zero times only translates what was already known,
    which is exactly the case that left a new disc half translated.
    """
    try: wanted = int(os.getenv('PHANTOM_DC_LEARN_ROUNDS', LEARNING_ROUNDS))
    except ValueError: wanted = LEARNING_ROUNDS
    return max(1, wanted)


def build_learned(image: Path, boot: bytes, boot_path: Path, info: dict, work: Path, env: dict, log) -> Path:
    """Build a disc with no hand-written profile, learning what it loads."""
    import apprentissage as learn
    irq=learn.find_katana_irq(boot)
    log('Routine d’interruption Sega reconnue sur le disque.' if irq else
        'Routine d’interruption Sega absente : le moteur utilisera celle que le jeu installe.')
    learned=learn.Learned(learn.cache_root(info['boot_sha1']))
    played=learned.merge_sessions(0x8c010000+len(boot))
    if played: log(f'{played} point(s) d’entrée découvert(s) pendant tes parties.')
    if learned.blocks or learned.main:
        log(f'Reprise de ce qui a été appris sur ce disque : {len(learned.blocks)} bloc(s) de code, '
            f'{len(learned.main)} entrée(s) du programme principal.')
    boot_end=0x8c010000+len(boot)
    game=work/'game'; build_dir=work/'build'; main_cache=work/'principal'
    options=['--irq-on-sr','--runtime-literals','--sp-loads','--setjmp-sites','--relocatable']
    def recompile(binary, src, inc, base, entries):
        run_command([sys.executable,LAB/'dcrecomp/tools/static_recompile.py',binary,src,inc,
                     '--base',hex(base),'--entries',entries,*options],env=env)
    configured=False
    exe=build_dir/'pal_probe.exe'
    # A kept work folder must not reuse code from an older translator.
    translator=hashlib.sha1((LAB/'dcrecomp/tools/static_recompile.py').read_bytes()
                            +' '.join(options).encode()).hexdigest()
    rounds=learning_rounds()
    for attempt in range(rounds+1):
        entries=sorted(learned.main|({irq} if irq else set())|learn.block_pointers(learned.blocks,boot)
                       |learn.code_constants(boot))
        entries_text=''.join(f'0x{e:08X}\n' for e in entries)
        main_dir=main_cache/hashlib.sha1((translator+entries_text).encode()).hexdigest()[:12]
        if not main_dir.exists():
            log('Traduction du programme SH-4 en code natif…')
            partial=main_dir.with_name(main_dir.name+'.part')
            if partial.exists(): shutil.rmtree(partial)
            partial.mkdir(parents=True)
            (partial/'entrees.txt').write_text(entries_text,encoding='ascii')
            recompile(boot_path,partial/'src/game',partial/'include/game',0x8c010000,partial/'entrees.txt')
            for old in main_cache.iterdir():
                if old.is_dir() and old != partial: shutil.rmtree(old)
            partial.rename(main_dir)
        stage=work/'etape'
        if stage.exists(): shutil.rmtree(stage)
        shutil.copytree(main_dir/'src',stage/'src'); shutil.copytree(main_dir/'include',stage/'include')
        table=[]
        if learned.blocks: log(f'Traduction de {len(learned.blocks)} bloc(s) de code chargé(s) par le jeu…')
        for block in learned.blocks: table+=learn.translate_block(block,work,recompile,stage,translator)
        # 1 TA buffer, 4 threads, 8 accurate P4, 16 interrupts through the
        # game's own vector (what it hangs off it only runs that way).
        learn.write_learned_table(stage,table); learn.patch_dispatch(stage); learn.write_profile(stage,irq,29)
        (stage/'tools').mkdir()
        shutil.copy2(LAB/'dcrecomp/tools/generate_stubs.py',stage/'tools/generate_stubs.py')
        run_command([sys.executable,stage/'tools/generate_stubs.py'],env=env)
        learn.dynamic_stubs(stage)
        learn.sync_tree(stage,game)
        if not configured:
            run_command([TOOLS/'cmake.exe','-S',PROTOTYPE,'-B',build_dir,'-G','Ninja',
                         '-DCMAKE_BUILD_TYPE=Debug','-DCMAKE_C_COMPILER='+str(TOOLS/'clang.exe'),
                         '-DCMAKE_MAKE_PROGRAM='+str(TOOLS/'ninja.exe'),
                         '-DPAL_GENERATED_DIR='+str(game),'-DPHANTOM_DC_GENERIC_TEST=ON'],env=env)
            configured=True
        log('Compilation du moteur Windows…')
        run_command([TOOLS/'cmake.exe','--build',build_dir,'--target','pal_probe','--parallel','4'],
                    env=env,timeout=5400,on_line=log)
        if attempt == rounds: break
        for dll in (LIBCHDR,SDL_DLL): shutil.copy2(dll,build_dir/dll.name)
        log(f'Apprentissage : essai {attempt+1} du jeu en arrière-plan (environ 1 min 30)…')
        run_dir=work/f'essai-{attempt+1}'
        learn.learning_run(exe,boot_path,image,run_dir)
        new=learned.merge_run(run_dir,boot_end)
        learned.save()
        if not new:
            log('Aucun nouveau code découvert : apprentissage terminé.'); break
        log(f'{new} nouveau(x) point(s) d’entrée découvert(s) ; nouvelle traduction.')
    return exe


LEARNING_OPTIONS = ['--irq-on-sr','--runtime-literals','--sp-loads','--setjmp-sites','--relocatable']


def _translator_stamp() -> str:
    """Identify the translator so a kept work folder never reuses old code."""
    return hashlib.sha1((LAB/'dcrecomp/tools/static_recompile.py').read_bytes()
                        + ' '.join(LEARNING_OPTIONS).encode()).hexdigest()


def wire_learned(learned, work, game, env, boot, log):
    """Translate what this disc taught us and hook it into a hand-written profile.

    A profile build translates the boot program and the programmes it knows the
    disc loads. Everything the game reached afterwards - during your own games,
    recorded by the launcher, or during the compiler's own trial runs - is
    translated here and reached through the dispatcher exactly as in a build
    with no hand-written profile, so no disc is left out of the learning.
    """
    import apprentissage as learn
    played = learned.merge_sessions(0x8c010000 + len(boot))
    if played:
        log(f'{played} point(s) d’entrée découvert(s) pendant tes parties.')
    if learned.blocks or learned.main:
        log(f'Reprise de ce qui a été appris sur ce disque : {len(learned.blocks)} bloc(s) de code, '
            f'{len(learned.main)} entrée(s) du programme principal.')
    translator = _translator_stamp()
    def recompile(binary, src, inc, base, entries):
        run_command([sys.executable, LAB/'dcrecomp/tools/static_recompile.py', str(binary), str(src), str(inc),
                     '--base', hex(base), '--entries', str(entries), *LEARNING_OPTIONS], env=env)
    table = []
    if learned.blocks:
        log(f'Traduction de {len(learned.blocks)} bloc(s) de code chargé(s) par le jeu…')
    for block in learned.blocks:
        table += learn.translate_block(block, work, recompile, game, translator)
    learn.write_learned_table(game, table)
    learn.patch_dispatch(game)
    learn.dynamic_stubs(game)
    return len(table)


def _build_profile(boot,boot_path,work,game,source,headers,env,log,generic,house,grandia,overlays,learned=None):
    """Build a disc whose profile was written by hand (Crazy Taxi, House)."""
    recompile=[sys.executable,LAB/'dcrecomp/tools/static_recompile.py',boot_path,source,headers]
    if house:
        files=[boot]+overlays
        main_entries=pointer_entries(files,0x0c010000,0x0c010000+len(boot))
        main_entries.update((0x8c01e310,0x8c0c41fc,0x8c01f8a8,0x8c02a850,0x8c0bbb90,0x8c0afaa4,0x8c0ad252))
        entries_path=work/'main-entries.txt'; save_entries(entries_path,main_entries)
        recompile.extend(['--entries',entries_path])
    elif grandia:
        main_entries_path=work/'main-entries.txt'
        save_entries(main_entries_path,{0x8c0f8ef0,0x8c0358f4,0x8c024fc6})
        recompile.extend(['--entries',main_entries_path])
    elif not generic: recompile.extend(['--entries',LAB/'europe-entries.txt'])
    run_command(recompile,env=env)
    if house:
        log('Traduction des programmes chargés depuis le disque…')
        overlay_entries=pointer_entries(files,0x0c42e800,0x0c42e800+len(overlays[1]))
        overlay_entries_path=work/'overlay-entries.txt'; save_entries(overlay_entries_path,overlay_entries)
        for i, data in enumerate(overlays,1):
            overlay_bin=work/f'overlay{i}.bin'; overlay_bin.write_bytes(data)
            overlay_dir=work/f'overlay{i}'
            cmd=[sys.executable,LAB/'dcrecomp/tools/static_recompile.py',overlay_bin,
                 overlay_dir/'src/game',overlay_dir/'include/game','--base','0x8c42e800']
            if i == 2: cmd.extend(['--entries',overlay_entries_path])
            run_command(cmd,env=env)
        profile_env=env.copy(); profile_env['PHANTOM_HOUSE_PROFILE_ROOT']=str(work)
        run_command([sys.executable,PROTOTYPE/'assemble_house_profile.py'],env=profile_env)
    if grandia:
        log('Traduction des programmes chargés depuis le disque…')
        files=[boot]+overlays
        for i, (data, (_, _, base, _)) in enumerate(zip(overlays, GRANDIA_OVERLAYS), 1):
            overlay_bin=work/f'overlay{i}.bin'; overlay_bin.write_bytes(data)
            overlay_dir=work/f'overlay{i}'
            entries=pointer_entries(files,base & 0x1fffffff,(base & 0x1fffffff)+len(data))
            entries_path=work/f'overlay{i}-entries.txt'; save_entries(entries_path,entries)
            run_command([sys.executable,LAB/'dcrecomp/tools/static_recompile.py',overlay_bin,
                         overlay_dir/'src/game',overlay_dir/'include/game',
                         '--base',hex(base),'--entries',entries_path],env=env)
        profile_env=env.copy(); profile_env['PHANTOM_DISC_PROFILE_ROOT']=str(work)
        run_command([sys.executable,PROTOTYPE/'assemble_disjoint_overlays.py'],env=profile_env)
    if not generic:
        hooks_path=PROTOTYPE/'apply_fiber_hooks.py'
        spec=importlib.util.spec_from_file_location('phantom_dc_hooks',hooks_path)
        hooks=importlib.util.module_from_spec(spec);spec.loader.exec_module(hooks)
        hooks.apply_hooks(source)
        texture_spec=importlib.util.spec_from_file_location('phantom_dc_texture_hooks',PROTOTYPE/'apply_texture_hooks.py')
        texture_hooks=importlib.util.module_from_spec(texture_spec);texture_spec.loader.exec_module(texture_hooks)
        texture_hooks.apply_hooks(source)
    if generic:
        import apprentissage as learn
        learn.write_profile(game,None,0)
        learn.write_learned_table(game,[])
    tools_dir=game/'tools'; tools_dir.mkdir()
    shutil.copy2(LAB/'dcrecomp/tools/generate_stubs.py',tools_dir/'generate_stubs.py')
    run_command([sys.executable,tools_dir/'generate_stubs.py'],env=env)
    # Même apprentissage que pour un jeu sans profil écrit à la main : ce que
    # le disque nous a appris est traduit et branché avant de compiler.
    if learned is not None: wire_learned(learned,work,game,env,boot,log)
    build_dir=work/'build'
    log('Compilation du moteur de test Windows…')
    run_command([TOOLS/'cmake.exe','-S',PROTOTYPE,'-B',build_dir,'-G','Ninja',
                 '-DCMAKE_BUILD_TYPE=Debug','-DCMAKE_C_COMPILER='+str(TOOLS/'clang.exe'),
                 '-DCMAKE_MAKE_PROGRAM='+str(TOOLS/'ninja.exe'),
                 '-DPAL_GENERATED_DIR='+str(game),'-DPHANTOM_DC_GENERIC_TEST='+('ON' if generic else 'OFF')],env=env)
    run_command([TOOLS/'cmake.exe','--build',build_dir,'--target','pal_probe','--parallel','4'],env=env,on_line=log)
    exe=build_dir/'pal_probe.exe'
    return exe


def build(image: Path, destination: Path, log=print) -> Path:
    image=image.resolve(strict=True)
    destination=destination.absolute()
    if image.suffix.lower() != '.chd':
        raise ValueError('Sélectionne un CHD Dreamcast.')
    if destination.exists():
        raise FileExistsError('Le dossier de destination existe déjà ; choisis un nouveau dossier.')
    if not destination.parent.is_dir():
        raise FileNotFoundError('Le dossier parent de destination est introuvable.')
    with DreamcastChd(image,LibChdr(LIBCHDR.resolve(strict=True))) as disc:
        info,boot=identify_with_boot(disc)
        profile=select_profile(info,boot)
        overlays=[]
        if profile == 'house_pal_experimental':
            for fad, count, checksum in HOUSE_OVERLAYS:
                data=read_extent(disc,fad-150,count*2048,600000)
                if hashlib.sha1(data).hexdigest() != checksum:
                    overlays=[]; profile='generic_experimental'; break
                overlays.append(data)
        elif profile == 'grandia_pal_experimental':
            for fad, count, base, checksum in GRANDIA_OVERLAYS:
                data=read_extent(disc,fad-150,count*2048,600000)
                if hashlib.sha1(data).hexdigest() != checksum:
                    overlays=[]; profile='generic_experimental'; break
                overlays.append(data)
    generic=profile != 'crazy_taxi_pal'
    house=profile == 'house_pal_experimental'
    grandia=profile == 'grandia_pal_experimental'
    learning=profile == 'generic_experimental'
    log('Image vérifiée : ' + info['title'] + ('. Mode expérimental : le lancement du jeu n’est pas garanti.' if generic else ' — version Europe prise en charge par le prototype.'))
    log(f'Apprentissage : ce disque sera lancé jusqu’à {learning_rounds()} fois pendant la compilation, '
        'et tout le code qu’il atteint sera traduit.')
    env=os.environ.copy(); env['PATH']=str(TOOLS)+os.pathsep+env.get('PATH','')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    # Building and publication happen on the destination volume. The exclusive
    # final rename cannot replace an existing directory on Windows.
    # PHANTOM_DC_KEEP_WORK keeps the work folder between builds (development).
    keep=os.getenv('PHANTOM_DC_KEEP_WORK')
    if keep: Path(keep).mkdir(parents=True,exist_ok=True); shutil.rmtree(Path(keep)/'result',ignore_errors=True)
    with (contextlib.nullcontext(keep) if keep else
          tempfile.TemporaryDirectory(prefix='.phantom-dreamcast-',dir=destination.parent)) as temp:
        work=Path(temp); game=work/'game'; source=game/'src/game'; headers=game/'include/game'
        boot_path=work/'1ST_READ.BIN'; boot_path.write_bytes(boot)
        if learning:
            exe=build_learned(image,boot,boot_path,info,work,env,log)
        else:
            import apprentissage as learn
            learned=learn.Learned(learn.cache_root(info['boot_sha1']))
            exe=_build_profile(boot,boot_path,work,game,source,headers,env,log,generic,house,grandia,overlays,learned)
            # Un profil écrit à la main apprend aussi : le jeu tourne une fois
            # de plus, ce qu'il atteint est traduit, et le moteur est recompilé
            # dans la même compilation. Rien n'est laissé pour la fois suivante.
            boot_end=0x8c010000+len(boot)
            for attempt in range(learning_rounds()):
                if not exe.exists(): break
                for dll in (LIBCHDR,SDL_DLL): shutil.copy2(dll,exe.parent/dll.name)
                log(f'Apprentissage : essai {attempt+1} du jeu en arrière-plan (environ 1 min 30)…')
                run_dir=work/f'essai-{attempt+1}'
                learn.learning_run(exe,boot_path,image,run_dir)
                new=learned.merge_run(run_dir,boot_end)
                learned.save()
                if not new:
                    log('Aucun nouveau code découvert : apprentissage terminé.'); break
                log(f'{new} nouveau(x) point(s) d’entrée découvert(s) ; nouvelle traduction.')
                wire_learned(learned,work,game,env,boot,log)
                run_command([TOOLS/'cmake.exe','--build',work/'build','--target','pal_probe','--parallel','4'],
                            env=env,on_line=log)
        output=work/'result';output.mkdir()
        shutil.copy2(exe,output/'Crazy Taxi - prototype.exe')
        shutil.copy2(boot_path,output/'1ST_READ.BIN')
        shutil.copy2(LIBCHDR,output/'libchdr.dll');shutil.copy2(SDL_DLL,output/'SDL2.dll')
        tests=('--test-runtime',) if generic else ('--test-irq','--test-tasks','--test-vmu-fibers','--test-aica-monitor','--test-texture-dma','--test-texture-reserve')
        for test in tests:
            run_command([output/'Crazy Taxi - prototype.exe',test],env=env,timeout=30)
        (output/'prototype.json').write_text(json.dumps({'console':'Dreamcast','titre':info['title'],
            'sha1_demarrage':info['boot_sha1'],'disque_original':str(image),'profil':profile,
            'statut':'prototype local : visuels, son, sauvegardes et intégration restent expérimentaux'},
            ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        try: output.rename(destination)
        except FileExistsError: raise FileExistsError('Le dossier de destination a été créé entre-temps.') from None
    log('Programme expérimental construit ; compatibilité du jeu non vérifiée.' if generic else 'Prototype construit et tests de base réussis.')
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path)
    parser.add_argument('dossier_de_test',type=Path)
    args=parser.parse_args()
    try: build(args.image,args.dossier_de_test)
    except (ChdError,OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print('Erreur : '+str(exc),file=sys.stderr);return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
