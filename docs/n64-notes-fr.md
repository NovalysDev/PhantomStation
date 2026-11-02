# Recherche N64 : GoldenEye 007 Europe

Cette recherche est séparée du compilateur PhantomStation. Elle ne crée pas
encore de jeu jouable et ne permet pas de compiler d'autres jeux Nintendo 64.

Le script `preparer_goldeneye_pal.py` vérifie la version européenne exacte,
extrait ses données de démarrage compressées et prépare des ELF MIPS pour
les deux premières fonctions. Les fichiers générés contiennent des données issues du
jeu personnel : ils doivent rester privés. L'original n'est jamais modifié.

La routine d'entrée produite a été traduite en C avec N64Recomp, puis exécutée
dans un banc d'essai qui vérifie l'effacement de la mémoire, la pile et le saut
vers la fonction suivante. Ce test ne lance pas le jeu.

La fonction suivante installe une entrée TLB puis saute vers `0x70000510`.
La traduction directe avec N64Recomp échoue sur l'instruction `mtc0`. Le correctif
expérimental `n64recomp-cop0-experimental.patch` ajoute au générateur C des
appels externes pour les registres utilisés par ce démarrage et pour `tlbwi`.
Le fichier `n64_tlb.hpp` est un premier modèle de traduction mémoire. Un banc
d'essai raccorde les deux fonctions traduites à ce modèle et vérifie le saut,
la mémoire et les registres. Ce n'est pas encore un moteur complet : les accès
mémoire du jeu et les autres instructions TLB ne sont pas raccordés.

Pour reproduire avec un N64Recomp compilé et `clang` déjà disponibles :

```powershell
python n64-recomp-lab/preparer_goldeneye_pal.py "CHEMIN/GoldenEye.z64" "CHEMIN/NOUVEAU-DOSSIER"
cd "CHEMIN/NOUVEAU-DOSSIER"
& "CHEMIN/N64Recomp.exe" eu-boot.toml
clang -std=c17 -O2 -I "CHEMIN/n64-recomp-lab/test" -I generated "CHEMIN/n64-recomp-lab/test/test_demarrage.c" generated/funcs_0.c -o test_demarrage.exe
./test_demarrage.exe
```

Depuis la racine de PhantomStation, le modèle TLB peut aussi être testé avec :

```powershell
./toolchain/bin/clang++.exe -std=c++20 -O2 -static n64-recomp-lab/test/test_tlb.cpp -o test_tlb.exe
./test_tlb.exe
```

Le dossier de destination doit être nouveau. Le script refuse toute image qui
ne correspond pas à la version européenne vérifiée. La version de N64Recomp
testée vient du dépôt officiel au commit `ffb39cdad1da5de07eaaa48bd1db4a89a7986771`,
avec ses sous-modules et le correctif expérimental, compilée en mode Release.
Le correctif attend des fonctions `phantom_cop0_write` et `phantom_tlbwi` du
programme hôte ; seul le banc d'essai les fournit actuellement. Les outils
du projet de recompilation GoldenEye comprennent
un exécutable Windows de débogage qui demande des bibliothèques de débogage
Microsoft absentes sur ce PC ; il n'a donc pas été utilisé pour ce test.

Travail restant : construire les symboles et les segments propres à la version
PAL, traiter son mécanisme TLB, adapter les correctifs et le moteur Windows,
puis tester le jeu et son intégration au lanceur. Le premier segment ne suffit
pas pour déduire automatiquement les adresses de toutes les fonctions.

Références : https://github.com/N64Recomp/N64Recomp et
https://github.com/n64decomp/007.

## Moteur commun expérimental

Le dossier `moteur-n64-commun` conserve les sources de native-console au commit
`13b28284c0380016a8f0d568ed7a089f62227d09`. Le compilateur et SDL2 sont dans
`../n64-engine/generique`. Le moteur produit un programme hybride : il compile
les instructions observées ou trouvées par analyse et interprète le reste.
La création d'un programme ne démontre pas qu'un jeu est jouable.

Le fichier `runtimes/runtime-n64/src/n64_recomp.c` vérifie les traductions des
pages traversées par un bloc recompilé. Sans ce correctif, deux pages virtuelles
non contiguës physiquement pouvaient exécuter une mauvaise instruction. Le test
`tests/n64/n64_recomp_tlb_test.c` reproduit ce cas et un changement ultérieur
de correspondance. Les dix tests N64 ciblés réussissent. Une comparaison avec
l'interpréteur du même moteur correspond sur 1 200 images de GoldenEye Europe ;
elle ne prouve pas la compatibilité complète avec la console ou d'autres jeux.
