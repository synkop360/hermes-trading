# Supervision locale sur Railway

Le mode `transport: local` du fichier de lancement utilise les recepteurs
natifs existants pour les instantanes, la publication, les couts, l'acces Nous
et la sante. Le lanceur conserve la validation du proprietaire, du compte,
des sources et des limites. Les cinq clotures uniques, les revues pendantes,
les compteurs et la publication par comparaison restent dans leurs journaux.

Le service s'execute avec :

```sh
/app/.venv/bin/python -m scripts.paper_supervisor_service --launch-file /app/state/cloud-supervisor-launch.json
```

Il doit partager le conteneur et le volume du moteur. Le chemin de supervision
attendu reste `/app/state/paired-supervisor/<experiment>/adaptive_200`.
Le mode `local` ne charge aucun `.env`. Les seuls journaux publics de ce service
sont des codes de statut. Un resultat incertain termine sa boucle ; les etats
de revue natifs permettent ensuite une reconciliation explicite.

Les delais de transport respectent le budget restant. Sur Linux, le lanceur
possede un groupe de processus ; les recepteurs imbriques restent dans ce groupe.
Sur Windows, un Job Object contient les descendants avant reprise du processus.
Une annulation ou un depassement de sortie arrete les descendants.

## Authentification persistante

Le Dockerfile installe uniquement le code client Hermes epingle au commit
`eb52760564dbba2e5971fa54bd67384e281cd3b8`, sans modifier les dependances
verrouillees du moteur, via l'installation editable officiellement supportee.
Il verifie que ses modules d'authentification s'importent.
Le parcours paper utilise son resolveur Nous et son verrou de renouvellement ;
il ne lance pas l'agent, ses outils ou ses fonctions de facturation.

Le lanceur local impose `HERMES_HOME=/app/state/cloud-auth` et
`HERMES_SHARED_AUTH_DIR=/app/state/cloud-auth/shared`. Il faut une session Nous
dediee dans cet emplacement avant activation. Ne pas dupliquer le jeton de
renouvellement du PC : les jetons Nous tournent et une double utilisation peut
revoquer la session. Un simple jeton d'acces court ne suffit pas a une execution
pendant la nuit.

Le mode optionnel `HERMES_NOUS_AUTH=environment` accepte les deux secrets
`HERMES_NOUS_API_KEY` et `HERMES_NOUS_PORTAL_TOKEN` pour un acces explicite
temporaire. Il refuse les secrets incomplets et le mode reel, sans repli sur
une autre identite. Il ne renouvelle pas ces secrets : pour la beta autonome,
utiliser le stockage d'authentification persistant et son resolveur officiel.
Le catalogue a prix nul et la souscription personnelle sans abonnement sont
toujours verifies avant tout renouvellement de preuve gratuite.

## Ordre du basculement

1. Conserver le checkpoint ferme du superviseur PC, ses 19 fichiers et leurs
   empreintes. Le checkpoint reste prive, hors GitHub et hors journaux Railway.
2. Terminer ou reconcilier la transition c7f816d vers 37e1be8 sur les etats fermes.
3. Appliquer la nouvelle transition de sources depuis 37e1be8. Le plan epingle
   la carte complete du successeur. Les 127 modules sans changement logique ont
   ete compares aux blobs Git de 37e1be8 apres normalisation LF ; seuls
   `proposal_transport.py` et `model_access_refresh.py` changent de comportement.
   Aucun compte ou historique ne doit etre reinitialise.
4. Relocaliser le journal et le fichier de lancement, avec `transport: local`,
   les chemins Linux et `wrapper_sha256` du nouveau lanceur. Conserver le fichier
   de lancement precedent et un recu de la relocation. Cette operation est
   distincte de la transition de sources, qui ne change que les liaisons de version.
5. Etablir la session Nous dediee. Demarrer le superviseur a cote du moteur et
   verifier sante fraiche, acces gratuit, couts et admission des entrees paper.
   Ne declarer la beta fonctionnelle qu'apres ces observations.

Ce changement ne realise pas automatiquement ces operations sur le volume actif.
Une fusion GitHub ou un conteneur sain ne constitue pas une preuve d'activation.
