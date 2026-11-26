from pathlib import Path
import base64,hashlib,importlib.util,json,os,re,subprocess,tempfile,time,uuid,urllib.error,urllib.request,urllib.parse,zipfile,argparse,sys
from phantom_nintendo import BASE,CSC,make_art
from detection_doublons import SOURCE_ENTRY,encoded_source

LAB=BASE/'test-local/dreamcast-research'

# Codes de triche : cherchés à chaque compilation, puis gardés sur place pour
# les suivantes. Le moteur lit « phantom-cheats.cht » à côté de son exécutable.
CODES_DOSSIER=LAB/'cheats'
CODES_URL='https://raw.githubusercontent.com/libretro/libretro-database/master/cht/Sega%20-%20Dreamcast/'
CODES_INDEX_URL='https://api.github.com/repos/libretro/libretro-database/contents/cht/Sega%20-%20Dreamcast'
CODES_INDEX=CODES_DOSSIER/'index-libretro-dreamcast.json'
CODES_REGIONS=frozenset(('europe','usa','japan','france','germany','spain','italy','pal','ntsc','en','fr','de','es','it','ja','jp','uk','australia'))
CODES_MOTS_VIDES=frozenset(('the','of','and','les','le','la','jeu','game','expert','edition','special','championship','championnat','version','tome'))
CODES_PREFERENCE=('europe','usa','japan')
# Jeux dont la version Dreamcast porte un autre nom en Amérique : la base
# libretro les classe sous ce nom-là, donc on l'essaie aussi.
CODES_AUTRES_NOMS={'v rally 2':'Test Drive V-Rally'}
# Disques déjà fournis avec leurs codes dans le projet, si la recherche par nom échoue.
CODES_CONNUS={'0cb24122c167a4f199a8aa4408dad4eeb604a0f3':'grandia_ii_europe.cht'}
# Codes des versions européennes (PAL), importés une fois pour toutes par
# importer_codes_pal.py : un .cht par jeu, nommé « Titre (Europe).cht ».
CODES_PAL=LAB/'codes-pal'
# « Grandia II » et « Grandia 2 » désignent le même jeu (pas « V », V-Rally).
CODES_ROMAINS={'ii':'2','iii':'3','iv':'4'}

def codes_normalise(texte):
    texte=texte.lower().replace('&',' and ')
    texte=re.sub(r'\([^)]*\)',' ',texte)
    texte=re.sub(r'[^a-z0-9]+',' ',texte)
    return ' '.join(CODES_ROMAINS.get(mot,mot) for mot in texte.split())

def codes_meme_jeu(nom_fichier,titre):
    """Vrai si le nom du .cht désigne ce jeu, à la région ou aux langues près."""
    a=codes_normalise(nom_fichier).split(); b=codes_normalise(titre).split()
    if not a or not b: return False
    court,long=(a,b) if len(a)<=len(b) else (b,a)
    if long[:len(court)]!=court: return False
    return all(mot in CODES_REGIONS for mot in long[len(court):])

def codes_mots(texte):
    """Mots significatifs d'un titre (les mentions de région et de version ne
    doivent pas décider du choix du fichier)."""
    return [mot for mot in codes_normalise(texte).split() if mot not in CODES_MOTS_VIDES]

def codes_index(log=print):
    """Liste des .cht Dreamcast de libretro-database, gardée un mois sur place."""
    noms=None
    if CODES_INDEX.is_file():
        try: noms=json.loads(CODES_INDEX.read_text(encoding='utf-8'))
        except (OSError,ValueError): noms=None
        if noms and time.time()-CODES_INDEX.stat().st_mtime<30*86400: return noms
    try:
        with urllib.request.urlopen(CODES_INDEX_URL,timeout=10) as reponse:
            entrees=json.loads(reponse.read().decode('utf-8'))
        recus=sorted(str(e.get('name','')) for e in entrees if str(e.get('name','')).endswith('.cht'))
        if recus:
            CODES_DOSSIER.mkdir(parents=True,exist_ok=True)
            CODES_INDEX.write_text(json.dumps(recus,ensure_ascii=False),encoding='utf-8')
            return recus
    except (urllib.error.URLError,OSError,ValueError) as erreur:
        if noms: return noms
        log('Recherche des codes hors ligne ('+str(getattr(erreur,'reason',erreur))+') : compilation sans codes.')
    return noms

def codes_region(texte):
    """Région indiquée entre parenthèses, par exemple (Europe) ou (USA)."""
    for region in re.findall(r'\(([^)]*)\)',texte.lower()):
        for mot in ('europe','usa','japan','france','germany','spain','italy'):
            if mot in region: return mot
    return None

def codes_region_normalise(region):
    """France, Allemagne, Espagne et Italie font partie de la version européenne."""
    return 'europe' if region in ('france','germany','spain','italy') else region

def codes_region_accepte(nom,region):
    """Un fichier sans région convient ; sinon il doit viser la même version."""
    autre=codes_region_normalise(codes_region(nom))
    return autre is None or autre==codes_region_normalise(region)

def codes_proche(titre,fichiers):
    """Fichier dont le nom a les mêmes mots significatifs que le titre (« The »
    en tête, ponctuation et mots vides mis à part), s'il est le seul aussi proche."""
    racine=lambda mots:[mot[:-1] if len(mot)>3 and mot.endswith('s') else mot for mot in mots]
    voulu=racine(codes_mots(titre))   # « Adventures » sur un site = « Adventure »
    if not voulu: return None
    chiffres=[mot for mot in voulu if mot.isdigit()]
    scores=[]
    for fichier in fichiers:
        mots=racine(codes_mots(fichier.stem))
        if not mots or [mot for mot in mots if mot.isdigit()]!=chiffres: continue
        commun=set(voulu)&set(mots)
        scores.append((min(len(commun)/len(set(voulu)),len(commun)/len(set(mots))),fichier.name,fichier))
    scores.sort(key=lambda s:(-s[0],s[1]))
    if scores and scores[0][0]>=0.75 and (len(scores)==1 or scores[0][0]>scores[1][0]): return scores[0][2]
    return None

def codes_pal(titre):
    """Codes importés pour la version européenne de ce jeu, s'il y en a."""
    if not CODES_PAL.is_dir(): return None
    if codes_region_normalise(codes_region(titre) or 'europe')!='europe': return None
    fichiers=sorted(CODES_PAL.glob('*.cht'))
    from compilation_dossier import sans_marque   # « Disney's Dinosaur » = « Dinosaur »
    for fichier in fichiers:
        if codes_meme_jeu(fichier.stem,titre) or codes_meme_jeu(sans_marque(fichier.stem),titre): return fichier
    return codes_proche(titre,fichiers)

def codes_local(titre):
    """Fichier de codes déjà présent dans le projet (cache des compilations)."""
    if not CODES_DOSSIER.is_dir(): return None
    region=codes_region_normalise(codes_region(titre) or 'europe')
    for fichier in sorted(CODES_DOSSIER.glob('*.cht')):
        if codes_meme_jeu(fichier.stem,titre) and codes_region_accepte(fichier.stem,region): return fichier
    return None

def codes_fichier(nom,log=print):
    """Télécharge un .cht précis et le garde pour les compilations suivantes."""
    if not nom or Path(nom).name!=nom or not nom.endswith('.cht'): return None
    cible=CODES_DOSSIER/nom
    if cible.is_file(): return cible
    try:
        with urllib.request.urlopen(CODES_URL+urllib.parse.quote(nom),timeout=10) as reponse:
            texte=reponse.read()
    except (urllib.error.URLError,OSError) as erreur:
        log('Téléchargement des codes impossible ('+str(getattr(erreur,'reason',erreur))+') : compilation sans codes.')
        return None
    if b'cheat0_' not in texte: return None
    CODES_DOSSIER.mkdir(parents=True,exist_ok=True)
    cible.write_bytes(texte)
    return cible

def codes_libretro(titre,log=print,region=None):
    """Cherche le .cht du jeu dans la liste de libretro-database."""
    noms=codes_index(log)
    if not noms: return None
    region=codes_region_normalise(region or codes_region(titre) or 'europe')
    trouves=[nom for nom in noms if codes_meme_jeu(nom[:-4],titre)]
    acceptes=[nom for nom in trouves if codes_region_accepte(nom[:-4],region)]
    if acceptes:
        acceptes.sort(key=lambda nom:(0 if codes_region_normalise(codes_region(nom))==region else 1,nom))
        return codes_fichier(acceptes[0],log)
    voulu=codes_mots(titre)
    if voulu:
        chiffres=[mot for mot in voulu if mot.isdigit()]
        scores=[]
        for nom in noms:
            if not codes_region_accepte(nom[:-4],region): continue
            mots=codes_mots(nom[:-4])
            if not mots or [mot for mot in mots if mot.isdigit()]!=chiffres: continue
            scores.append((len(set(voulu)&set(mots))/len(set(voulu)),nom))
        scores.sort(reverse=True)
        if scores and scores[0][0]>=0.75 and (len(scores)==1 or scores[0][0]>scores[1][0]):
            return codes_fichier(scores[0][1],log)
    if trouves:
        autres='/'.join(sorted({codes_region_normalise(codes_region(nom[:-4])) or 'sans region' for nom in trouves}))
        log('Codes trouvés seulement en version '+autres+' : ignorés pour cette version '+region+'.')
    return None

def codes_autres_noms(titre):
    """Autres noms connus du même jeu (versions américaines)."""
    autre=CODES_AUTRES_NOMS.get(' '.join(codes_mots(titre)))
    return [autre] if autre else []

def chercher_codes(titre,demarrage=None,log=print):
    """Codes du jeu : dossier cheats du projet, puis libretro-database.
    Le fichier trouvé est joint à la compilation et gardé pour les suivantes.
    Ne bloque jamais une compilation : hors ligne, elle continue sans codes."""
    fichier=codes_pal(titre)
    if fichier: return fichier
    fichier=codes_local(titre)
    if fichier: return fichier
    fichier=codes_libretro(titre,log)
    if fichier: return fichier
    region=codes_region(titre)
    for autre in codes_autres_noms(titre):
        fichier=codes_libretro(autre,log,region)
        if fichier: return fichier
    if demarrage is not None:
        try: empreinte=hashlib.sha1(Path(demarrage).read_bytes()).hexdigest()
        except OSError: empreinte=''
        connu=CODES_CONNUS.get(empreinte)
        if connu and (CODES_DOSSIER/connu).is_file(): return CODES_DOSSIER/connu
    return None

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

def build(image,name,output,icon=None,background=None,log=print,package_id=None,autres_disques=()):
    image=Path(image).resolve(strict=True);output=Path(output)
    if image.suffix.lower()!='.chd': raise ValueError('Choisis une image Dreamcast au format .chd.')
    # Jeu sur plusieurs disques : un seul programme compilé (celui du disque 1,
    # le même sur chaque disque), et tous les disques rangés dans le jeu.
    autres=[Path(d).resolve(strict=True) for d in autres_disques]
    if len(autres)>7: raise ValueError('Un jeu Dreamcast peut avoir au plus 8 disques.')
    if any(d.suffix.lower()!='.chd' for d in autres): raise ValueError('Chaque disque doit être une image Dreamcast au format .chd.')
    disques=[image]+autres
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
            if autres:
                programmes=[]
                for disque in disques:
                    with module.DreamcastChd(disque,module.LibChdr(module.LIBCHDR.resolve(strict=True))) as disc:
                        _,boot=module.identify_with_boot(disc)
                    programmes.append(hashlib.sha1(boot).hexdigest())
                if len(set(programmes))!=1:
                    raise ValueError('Ces disques n’ont pas le même programme : ce ne sont pas les disques d’un même jeu.')
                log(f'{len(disques)} disques du même jeu : programme commun, F4 changera de disque en jeu.')
            native=module.build(image,work/'native',log=log)
            prototype=json.loads((native/'prototype.json').read_text(encoding='utf-8'))
            profile=prototype['profil']
            generic=profile!='crazy_taxi_pal'
            log('Recherche de la jaquette et création du lanceur…')
            make_art('DC',image,name,icon,work,log)
            build_stub(work/'icone.ico',partial,work,background,generic=generic,house=profile=='house_pal_experimental')
            runtime=native/'Crazy Taxi - prototype.exe'
            with runtime.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            game_id=uuid.UUID(hex=package_id).hex if package_id else uuid.uuid4().hex
            manifest=game_id+'\ndreamcast.exe\n'+digest+'\n'+base64.b64encode(name.encode()).decode()+'\ndocuments-saves-v1\n'
            config='[game]\nname = '+json.dumps(name,ensure_ascii=False)+'\nplatform = "DC"\nprofile = '+json.dumps(profile)+'\n'
            files=[(runtime,'dreamcast.exe'),(native/'1ST_READ.BIN','1ST_READ.BIN'),(image,'disque.chd')]+[(d,f'disque-{k}.chd') for k,d in enumerate(autres,2)]+[(native/'SDL2.dll','SDL2.dll'),(native/'libchdr.dll','libchdr.dll'),(work/'boxart.png','assets/img/boxart.png'),(work/'boxart.tga','assets/img/boxart.tga'),(LAB/'dcrecomp/LICENSE','licences/dcrecomp-LICENSE.txt'),(LAB/'glew-2.2.0/LICENSE.txt','licences/glew-LICENSE.txt'),(LAB/'libchdr-6cde5348eb118da3baf94f75a69577a005a484fd/LICENSE.txt','licences/libchdr-LICENSE.txt'),(BASE/'n64-engine/generique/SDL2/LICENSE.txt','licences/SDL2-LICENSE.txt')]
            # Le titre affiché n'a plus sa région (« Re-Volt ») : celle du nom du
            # fichier d'origine choisit la bonne version des codes (USA ≠ PAL).
            region=codes_region(Path(image).stem)
            titre_codes=name if codes_region(name) or not region else name+' ('+region+')'
            codes=chercher_codes(titre_codes,native/'1ST_READ.BIN',log=log)
            if codes is not None:
                files.append((codes,'phantom-cheats.cht'))
                log('Codes de triche inclus : '+codes.name)
            else:
                log('Aucun fichier de codes trouvé pour ce jeu : le menu F11 restera vide.')
            # L'apprentissage du disque et l'empreinte du moteur voyagent avec le
            # jeu : un autre PC ou une recompilation repart de ce qui a été
            # appris, et un jeu au moteur ancien est mis à jour après une partie.
            import apprentissage
            appris=apprentissage.fichiers_a_ranger(prototype.get('sha1_demarrage',''))
            files+=appris
            if appris: log(f'Apprentissage rangé dans le jeu : {len(appris)-1} bloc(s) de code.')
            log('Création et vérification du fichier unique…')
            with zipfile.ZipFile(partial,'a',allowZip64=True) as archive:
                archive.writestr('phantom-package.txt',manifest.encode())
                archive.writestr('game.toml',config.encode())
                archive.writestr(SOURCE_ENTRY,encoded_source('DC',[[str(d) for d in disques]]))
                archive.writestr('moteur.txt',str(prototype.get('moteur','')).encode('ascii'))
                if appris: archive.writestr('apprentissage/disque.txt',prototype['sha1_demarrage'].encode('ascii'))
                for path,arcname in files:
                    archive.write(path,arcname,compress_type=zipfile.ZIP_STORED if path in disques else zipfile.ZIP_DEFLATED)
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
    p.add_argument('--disque',action='append',default=[],help='disque suivant du même jeu (2, 3…)')
    a=p.parse_args()
    try:
        import erreurs_connues as apprentissage
        for ligne in apprentissage.preparer('DC',a.rom,a.nom): print(ligne,flush=True)
        print('PHANTOM_DREAMCAST_RESULT='+str(build(a.rom,a.nom,a.sortie,a.icone,a.fond,autres_disques=a.disque)),flush=True)
    except Exception as e:
        try:
            import apprentissage as _a; print(_a.noter_echec('DC',a.rom,a.nom,str(e)),flush=True)
        except Exception: pass
        print('ERREUR : '+str(e),file=sys.stderr,flush=True);return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
