# Sauvegarde Event

Copie les photos d'un event du PC Kadra vers le dossier de tri du disque dur,
toutes les 10 minutes, sans qu'on ait à y penser.

Pour l'event choisi, trois dossiers sont tenus à jour :

| Source (PC Kadra)        | Destination (dossier de tri) |
|--------------------------|------------------------------|
| `depot\<EVENT>`          | `<EVENT>_HD`                 |
| `redim\<EVENT>`          | `<EVENT>_REDIM`              |
| `phase 1\python\data`    | `<EVENT>_DATA`               |

Le contenu arrive directement dans ces dossiers, sans niveau ajouté. La copie
est faite par `robocopy`, livré avec Windows.

## Installer

Télécharger **`Sauvegarde Event.exe`** depuis la page
[Releases](https://github.com/hlelouarn/sauvegarde-event/releases/latest)
(le dépôt est privé : il faut être connecté à GitHub) et le poser où l'on veut,
sur le Bureau par exemple. Rien d'autre à installer.

Au premier lancement, Windows affiche « Windows a protégé votre ordinateur »
(SmartScreen), parce que l'exe n'est pas signé : cliquer sur
**Informations complémentaires**, puis **Exécuter quand même**.

## Régler

Au premier lancement sur un PC, la fenêtre des réglages s'ouvre : on y indique
les trois dossiers source et le dossier de tri. C'est fait une fois par PC ;
le bouton **Réglages** permet d'y revenir.

Tout est rangé dans `%APPDATA%\SauvegardeEvent\` :

- `config.json` : les chemins et les options (automatique, intervalle, miroir) ;
- `sauvegarde.log` : le journal des copies.

## Le miroir et son garde-fou

Avec **Miroir** coché, ce qui est supprimé sur le PC est aussi supprimé sur le
disque. Si une copie devait ainsi effacer plus de 80 % des photos déjà
sauvegardées d'un dossier (source vidée par erreur, mauvais dossier…), la
suppression est bloquée : seules les nouveautés sont copiées et un message
prévient. Si la suppression était voulue, le ménage se fait à la main.

## Publier une version

1. Changer `VERSION` en tête de `sauvegarde_event.pyw`, committer, pousser.
2. Poser le tag du même numéro et le pousser :

   ```bash
   git tag v1.0.1
   ```

   ```bash
   git push origin v1.0.1
   ```

GitHub compile l'exe et l'attache à la Release. Si le tag et `VERSION` ne
portent pas le même numéro, le build échoue.
