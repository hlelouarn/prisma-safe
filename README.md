# Prisma Safe

Copie les photos d'un event du PC Kadra vers le dossier de tri du disque dur,
toutes les 10 minutes, sans qu'on ait à y penser. (L'outil s'appelait
« Sauvegarde Event » en version 1.0.0.)

Pour l'event choisi, trois dossiers sont tenus à jour :

| Source (PC Kadra)        | Destination (dossier de tri) |
|--------------------------|------------------------------|
| `depot\<EVENT>`          | `<EVENT>_HD`                 |
| `redim\<EVENT>`          | `<EVENT>_REDIM`              |
| `phase 1\python\data`    | `<EVENT>_DATA`               |

Le contenu arrive directement dans ces dossiers, sans niveau ajouté. La copie
est faite par `robocopy`, livré avec Windows.

## Installer

Télécharger **`Prisma.Safe.exe`** depuis la page
[Releases](https://github.com/hlelouarn/prisma-safe/releases/latest) et le
poser où l'on veut, sur le Bureau par exemple. Rien d'autre à installer.
GitHub remplace l'espace du nom par un point ; on peut renommer le fichier
sans conséquence.

Au premier lancement, Windows affiche « Windows a protégé votre ordinateur »
(SmartScreen), parce que l'exe n'est pas signé : cliquer sur
**Informations complémentaires**, puis **Exécuter quand même**.

## Régler

Au premier lancement sur un PC, la fenêtre des réglages s'ouvre : on y indique
les trois dossiers source et le dossier de tri. C'est fait une fois par PC ;
le bouton **Réglages** permet d'y revenir.

La même fenêtre porte la case **Lancer au démarrage de Windows** : l'outil
démarre alors avec la session, sans ouvrir de fenêtre, et attend une minute
avant sa première copie (le temps que le disque soit là). Sous Windows 11,
son icône se range d'abord derrière la flèche **^** près de l'horloge ; on
peut la faire glisser à côté pour la garder en vue.

Tout est rangé dans `%APPDATA%\PrismaSafe\` :

- `config.json` : les chemins et les options (automatique, intervalle, miroir) ;
- `sauvegarde.log` : le journal des copies.

Un PC réglé avec Sauvegarde Event 1.0.0 garde ses chemins : ils sont repris au
premier lancement de Prisma Safe.

## Fermer n'arrête pas

La croix range la fenêtre près de l'horloge (zone de notification) et les
copies continuent. Un clic sur l'icône rouvre la fenêtre ; relancer l'exe
aussi. Pour arrêter vraiment : bouton **Quitter**, ou clic droit sur l'icône
puis **Quitter**.

Une erreur ou une alerte du garde-fou ne reste pas cachée : une notification
s'affiche, et l'alerte rouvre la fenêtre.

## Stop

Le bouton **Stop** (aussi au clic droit sur l'icône) arrête net la copie en
cours et décoche **Automatique** : plus rien ne part tout seul, même après un
redémarrage, tant qu'on ne l'a pas recoché. **Sauvegarder maintenant** reste
possible pour une copie à la demande.

Le fichier qui était en train d'être copié reste incomplet sur le disque ; la
copie suivante le reprend.

## L'event suit Kadra

En automatique, quand Kadra passe sur un autre event (son fichier
`current_event`), les copies suivent au tour suivant, et le journal le dit.
Un event choisi à la main dans la liste reste en place tant que Kadra n'en
change pas. Un event qui n'a pas encore de dossier n'est pas suivi tout de
suite : il le sera dès que ses premières photos arrivent.

## Le miroir et son garde-fou

Avec **Miroir** coché, ce qui est supprimé sur le PC est aussi supprimé sur le
disque. Si une copie devait ainsi effacer plus de 80 % des photos déjà
sauvegardées d'un dossier (source vidée par erreur, mauvais dossier…), la
suppression est bloquée : seules les nouveautés sont copiées, un message
prévient et la tuile du dossier passe à l'ambre.

Si la suppression était voulue (dossiers renommés ou rangés autrement, par
exemple), le bouton **Forcer la suppression…** de la tuile la fait : l'outil
recompte, montre combien de photos partiraient, de quelle source et de quel
dossier, et ne supprime qu'après un **Oui**. C'est définitif, rien ne passe
par la corbeille ; la réponse proposée par défaut est **Non**.

## Mise à jour

Au lancement, puis toutes les 6 heures, l'outil regarde s'il existe une
version plus récente sur GitHub. Si oui, un bandeau le dit : **Mettre à jour**
télécharge le nouvel exe, le vérifie, le met à la place de l'ancien et relance
l'application. Jamais pendant une copie : l'installation attend qu'elle finisse.

En bas de la fenêtre, à côté de **Quitter**, la version dit où elle en est :
« à jour », « 1.3.0 disponible », ou « mise à jour non vérifiée » quand GitHub
n'a pas répondu. Un clic dessus revérifie tout de suite. Le bandeau n'apparaît
que s'il existe une version plus récente.

Sans Internet, rien ne se passe et l'outil continue de copier.

## Publier une version

1. Changer `VERSION` en tête de `prisma_safe.pyw`, committer, pousser.
2. Poser le tag du même numéro et le pousser :

   ```bash
   git tag v1.2.1
   ```

   ```bash
   git push origin v1.2.1
   ```

GitHub lance les tests, compile l'exe et l'attache à la Release ; les postes
la verront à leur prochaine vérification. Si le tag et `VERSION` ne portent
pas le même numéro, ou si un test échoue, rien n'est publié.

Lancer les tests ici : `python -m unittest discover -s tests -v`.
