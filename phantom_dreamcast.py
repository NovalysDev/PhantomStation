from pathlib import Path
import base64,hashlib,importlib.util,json,os,re,subprocess,tempfile,uuid,zipfile,argparse,sys
from phantom_nintendo import BASE,CSC,make_art
from detection_doublons import SOURCE_ENTRY,encoded_source

LAB=BASE/'test-local/dreamcast-research'

def build_stub(icon,output,work,background=None,generic=False,house=False):
    source=(BASE/'unique_launcher.cs').read_text(encoding='utf-8-sig')
    target='            string game = task.Result;\n'
    if source.count(target)!=1: raise RuntimeError('Lanceur PhantomStation incompatible.')
    source=source.replace(target,target+'            Hide(); var gameWindow = new DreamcastGameForm(game); gameWindow.FormClosed += (s, e) => Close(); gameWindow.Show(); return;\n')
    source=source.replace("string name = Path.GetFileNameWithoutExtension(game).Replace('_', ' ').Trim();", "string name = DisplayTitle(PreparedGameTitle).Replace('_', ' ').Trim();")
    if source.count('clock.Elapsed.TotalSeconds < 8.4') != 1: raise RuntimeError('Durée de l’introduction Dreamcast introuvable.')
    source=source.replace('clock.Elapsed.TotalSeconds < 8.4','clock.Elapsed.TotalSeconds < 9.0')
    cs=work/'lanceur.cs';cs.write_text(source,encoding='utf-8')
    assets=BASE/'assets'; art=Path(background) if background else assets/'phantom-startup.png'
    cmd=[str(CSC),'/nologo','/target:winexe','/out:'+str(output),'/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll', '/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll','/win32icon:'+str(icon),'/resource:'+str(art)+',art','/resource:'+str(assets/'logo-dreamcast.png')+',logo','/resource:'+str(assets/'son-demarrage-dreamcast.wav')+',sound',str(cs),str(BASE/'phantom_dreamcast_launcher.cs'),str(BASE/'phantom_dreamcast_vmu.cs'),str(BASE/'phantom_embedded_game.cs')]
    if generic: cmd.insert(1,'/define:PHANTOM_DC_GENERIC'+(';PHANTOM_DC_HOUSE' if house else ''))
    result=subprocess.run(cmd,capture_output=True,text=True,errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise RuntimeError('Création du lanceur Dreamcast impossible : '+result.stdout+'\n'+result.stderr)

def build(image,name,output,icon=None,background=None,log=print,package_id=None):
    image=Path(image).resolve(strict=True);output=Path(output)
    if image.suffix.lower()!='.chd': raise ValueError('Choisis une image Dreamcast au format .chd.')
    output.mkdir(parents=True,exist_ok=True)
    filename=re.sub(r"[^\w. '’-]+",'-',name,flags=re.UNICODE).strip(' .') or 'Jeu Dreamcast'
    destination=output/(filename+'.exe');number=2
    while destination.exists():
        destination=output/(filename+f' ({number}).exe');number+=1
    partial=output/(destination.name+'.'+uuid.uuid4().hex+'.part')
    try:
        with tempfile.TemporaryDirectory(prefix='phantom-dreamcast-package-') as tmp:
            work=Path(tmp)
            spec=importlib.util.spec_from_file_location('phantom_dc_builder',LAB/'build_pal_prototype.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            native=module.build(image,work/'native',log=log)
            profile=json.loads((native/'prototype.json').read_text(encoding='utf-8'))['profil']
            generic=profile!='crazy_taxi_pal'
            log('Recherche de la jaquette et création du lanceur…')
            make_art('DC',image,name,icon,work,log)
            build_stub(work/'icone.ico',partial,work,background,generic=generic,house=profile=='house_pal_experimental')
            runtime=native/'Crazy Taxi - prototype.exe'
            with runtime.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            game_id=uuid.UUID(hex=package_id).hex if package_id else uuid.uuid4().hex
            manifest=game_id+'\ndreamcast.exe\n'+digest+'\n'+base64.b64encode(name.encode()).decode()+'\ndocuments-saves-v1\n'
            config='[game]\nname = '+json.dumps(name,ensure_ascii=False)+'\nplatform = "DC"\nprofile = '+json.dumps(profile)+'\n'
            files=[(runtime,'dreamcast.exe'),(native/'1ST_READ.BIN','1ST_READ.BIN'),(image,'disque.chd'),(native/'SDL2.dll','SDL2.dll'),(native/'libchdr.dll','libchdr.dll'),(work/'boxart.png','assets/img/boxart.png'),(work/'boxart.tga','assets/img/boxart.tga'),(LAB/'dcrecomp/LICENSE','licences/dcrecomp-LICENSE.txt'),(LAB/'glew-2.2.0/LICENSE.txt','licences/glew-LICENSE.txt'),(LAB/'libchdr-6cde5348eb118da3baf94f75a69577a005a484fd/LICENSE.txt','licences/libchdr-LICENSE.txt'),(BASE/'n64-engine/generique/SDL2/LICENSE.txt','licences/SDL2-LICENSE.txt')]
            log('Création et vérification du fichier unique…')
            with zipfile.ZipFile(partial,'a',allowZip64=True) as archive:
                archive.writestr('phantom-package.txt',manifest.encode())
                archive.writestr('game.toml',config.encode())
                archive.writestr(SOURCE_ENTRY,encoded_source('DC',[[image]]))
                for path,arcname in files:
                    archive.write(path,arcname,compress_type=zipfile.ZIP_STORED if path==image else zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(partial) as archive:
                bad=archive.testzip()
                if bad: raise RuntimeError('Fichier intégré endommagé : '+bad)
                with archive.open('dreamcast.exe') as f: actual=hashlib.file_digest(f,'sha256').hexdigest()
                if actual!=digest: raise RuntimeError('La vérification du programme Dreamcast a échoué.')
            if partial.stat().st_size>=2**32: raise RuntimeError('Ce prototype ne prend pas encore en charge les fichiers uniques de 4 Gio ou plus.')
            os.rename(partial,destination)
        log('Fichier expérimental créé et vérifié ; le fonctionnement du jeu reste à tester.' if generic else 'Jeu Dreamcast expérimental créé et vérifié.')
        return destination
    finally: partial.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',required=True);p.add_argument('--nom',required=True);p.add_argument('--sortie',required=True);p.add_argument('--icone');p.add_argument('--fond')
    a=p.parse_args()
    try:
        import erreurs_connues as apprentissage
        for ligne in apprentissage.preparer('DC',a.rom,a.nom): print(ligne,flush=True)
        print('PHANTOM_DREAMCAST_RESULT='+str(build(a.rom,a.nom,a.sortie,a.icone,a.fond)),flush=True)
    except Exception as e:
        try:
            import apprentissage as _a; print(_a.noter_echec('DC',a.rom,a.nom,str(e)),flush=True)
        except Exception: pass
        print('ERREUR : '+str(e),file=sys.stderr,flush=True);return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
