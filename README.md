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

## Le miroir et son garde-fou

Avec **Miroir** coché, ce qui est supprimé sur le PC est aussi supprimé sur le
disque. Si une copie devait ainsi effacer plus de 80 % des photos déjà
sauvegardées d'un dossier (source vidée par erreur, mauvais dossier…), la
suppression est bloquée : seules les nouveautés sont copiées et un message
prévient. Si la suppression était voulue, le ménage se fait à la main.

## Mise à jour

Au lancement, puis toutes les 6 heures, l'outil regarde s'il existe une
version plus récente sur GitHub. Si oui, un bandeau le dit : **Mettre à jour**
télécharge le nouvel exe, le vérifie, le met à la place de l'ancien et relance
l'application. Jamais pendant une copie : l'installation attend qu'elle finisse.

Sans Internet, rien ne se passe et l'outil continue de copier.

## Publier une version

1. Changer `VERSION` en tête de `prisma_safe.pyw`, committer, pousser.
2. Poser le tag du même numéro et le pousser :

   ```bash
   git tag v1.1.1
   ```

   ```bash
   git push origin v1.1.1
   ```

GitHub lance les tests, compile l'exe et l'attache à la Release ; les postes
la verront à leur prochaine vérification. Si le tag et `VERSION` ne portent
pas le même numéro, ou si un test échoue, rien n'est publié.

Lancer les tests ici : `python -m unittest discover -s tests -v`.
