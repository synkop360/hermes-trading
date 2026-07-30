from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPHIFY_OUT = ROOT / "graphify-out"
OBSIDIAN_OUT = GRAPHIFY_OUT / "obsidian"
OBSIDIAN_NOTES = OBSIDIAN_OUT / "notes"


DOCS = {
    "readme": "README.md",
    "architecture": "docs/ARCHITECTURE.md",
    "commands": "docs/COMMANDS.md",
    "deployment": "docs/DEPLOYMENT.md",
    "hermes": "docs/HERMES.md",
    "kraken": "docs/KRAKEN.md",
    "notifications": "docs/NOTIFICATIONS.md",
    "operations": "docs/OPERATIONS.md",
    "safety": "docs/SAFETY.md",
    "state_files": "docs/STATE_FILES.md",
    "troubleshooting": "docs/TROUBLESHOOTING.md",
    "goal": "state/goal.yaml",
    "strategy": "state/strategy.yaml",
}


CONCEPTS = [
    {
        "id": "hermes_trading_worker",
        "label": "Hermes Trading Worker",
        "source": "README.md",
        "summary": "Worker Python qui observe les marches Kraken, evalue une strategie RSI et ecrit les decisions dans l'etat persistant.",
        "tags": ["worker", "trading", "python"],
    },
    {
        "id": "multi_asset_training",
        "label": "Multi-Asset Training",
        "source": "state/goal.yaml",
        "summary": "Mode simule applique aux actifs BTC/USDT, ETH/USDT, XRP/USDT et SHIB/USDT.",
        "tags": ["training", "multi-asset"],
    },
    {
        "id": "kraken_market_data",
        "label": "Kraken Market Data",
        "source": "docs/KRAKEN.md",
        "summary": "Connexion Kraken via ccxt pour recuperer ticker et OHLCV sans envoyer d'ordres reels.",
        "tags": ["kraken", "ccxt", "market-data"],
    },
    {
        "id": "railway_deployment",
        "label": "Railway Deployment",
        "source": "docs/DEPLOYMENT.md",
        "summary": "Deploiement Railway du service hermes-trading avec volume persistant monte sur /app/state.",
        "tags": ["railway", "deployment"],
    },
    {
        "id": "railway_state_volume",
        "label": "Railway State Volume",
        "source": "docs/OPERATIONS.md",
        "summary": "Source de verite distante pour goal.yaml, strategy.yaml, heartbeat.json, decisions.jsonl, trades.jsonl et hypotheses.jsonl.",
        "tags": ["state", "volume"],
    },
    {
        "id": "hermes_flow",
        "label": "Hermes Flow",
        "source": "docs/HERMES.md",
        "summary": "Cron Hermes qui surveille le volume Railway et propose une modification de strategie quand les conditions de reflexion sont atteintes.",
        "tags": ["hermes", "cron", "reflection"],
    },
    {
        "id": "discord_notifications",
        "label": "Discord Notifications",
        "source": "docs/NOTIFICATIONS.md",
        "summary": "Systeme activable de notifications Discord webhook avec modes verbose, orders_only, pnl_only et resume gains/pertes periodique.",
        "tags": ["notifications", "discord", "operations"],
    },
    {
        "id": "pnl_summary_notifications",
        "label": "P&L Summary Notifications",
        "source": "docs/NOTIFICATIONS.md",
        "summary": "Resumes activables par cycles et par frequences standard comme hourly, 12h, 1d, 1w ou 1mo, avec decisions, trades, PnL, ordres et detail par actif.",
        "tags": ["notifications", "pnl", "summary"],
    },
    {
        "id": "low_funds_alerts",
        "label": "Low Funds Alerts",
        "source": "docs/NOTIFICATIONS.md",
        "summary": "Discord safety alerts for WARNING, CRITICAL, and RESOLVED funds levels based on available quote balance and order budget.",
        "tags": ["notifications", "safety", "funds"],
    },
    {
        "id": "strategy_yaml",
        "label": "strategy.yaml",
        "source": "state/strategy.yaml",
        "summary": "Strategie active RSI versionnee: seuil d'entree, direction, stop loss et taille de position.",
        "tags": ["state-file", "strategy"],
    },
    {
        "id": "goal_yaml",
        "label": "goal.yaml",
        "source": "state/goal.yaml",
        "summary": "Objectif de performance, actifs suivis et cadence de reflexion.",
        "tags": ["state-file", "goal"],
    },
    {
        "id": "decisions_jsonl",
        "label": "decisions.jsonl",
        "source": "docs/STATE_FILES.md",
        "summary": "Journal de toutes les decisions, y compris les no-trade decisions.",
        "tags": ["state-file", "decisions"],
    },
    {
        "id": "trades_jsonl",
        "label": "trades.jsonl",
        "source": "docs/STATE_FILES.md",
        "summary": "Journal des trades simules fermes uniquement; il peut rester vide si aucune entree n'est declenchee.",
        "tags": ["state-file", "trades"],
    },
    {
        "id": "orders_jsonl",
        "label": "orders.jsonl",
        "source": "docs/STATE_FILES.md",
        "summary": "Journal des tentatives d'execution: simulees en training, validees en live validation-only, puis soumises apres promotion manuelle.",
        "tags": ["state-file", "orders", "execution"],
    },
    {
        "id": "heartbeat_json",
        "label": "heartbeat.json",
        "source": "docs/STATE_FILES.md",
        "summary": "Dernier etat de sante du worker: actif, mode, execution, compteurs et timestamp.",
        "tags": ["state-file", "health"],
    },
    {
        "id": "no_trade_reflection",
        "label": "No-Trade Reflection",
        "source": "docs/HERMES.md",
        "summary": "Reflexion declenchee quand beaucoup de decisions ne produisent aucun trade ferme, signe possible d'une strategie trop stricte.",
        "tags": ["reflection", "no-trade"],
    },
    {
        "id": "live_trading_guardrails",
        "label": "Live Trading Guardrails",
        "source": "docs/SAFETY.md",
        "summary": "Le live exige deux confirmations, demarre en validation-only et demande un second verrou avant tout ordre reel.",
        "tags": ["safety", "live"],
    },
    {
        "id": "kraken_validation_only",
        "label": "Kraken Validation-Only",
        "source": "docs/KRAKEN.md",
        "summary": "Chemin live prudent qui appelle Kraken avec validate=true et journalise l'ordre sans le soumettre.",
        "tags": ["kraken", "validation", "orders"],
    },
    {
        "id": "local_vs_remote_state",
        "label": "Local vs Remote State",
        "source": "docs/TROUBLESHOOTING.md",
        "summary": "Le dossier local state/ peut etre en retard; le volume Railway /app/state est prioritaire pour le worker deploye.",
        "tags": ["operations", "troubleshooting"],
    },
]


EDGES = [
    ("hermes_trading_worker", "multi_asset_training", "implements"),
    ("hermes_trading_worker", "kraken_market_data", "uses"),
    ("hermes_trading_worker", "railway_state_volume", "writes"),
    ("hermes_trading_worker", "heartbeat_json", "updates"),
    ("hermes_trading_worker", "discord_notifications", "notifies"),
    ("discord_notifications", "pnl_summary_notifications", "publishes"),
    ("discord_notifications", "low_funds_alerts", "publishes"),
    ("multi_asset_training", "goal_yaml", "configured_by"),
    ("multi_asset_training", "strategy_yaml", "uses"),
    ("multi_asset_training", "decisions_jsonl", "records"),
    ("multi_asset_training", "trades_jsonl", "may_record"),
    ("multi_asset_training", "orders_jsonl", "may_record"),
    ("railway_deployment", "railway_state_volume", "mounts"),
    ("hermes_flow", "railway_state_volume", "reads"),
    ("hermes_flow", "strategy_yaml", "updates"),
    ("hermes_flow", "no_trade_reflection", "implements"),
    ("no_trade_reflection", "decisions_jsonl", "depends_on"),
    ("no_trade_reflection", "trades_jsonl", "compares_with"),
    ("live_trading_guardrails", "kraken_market_data", "limits"),
    ("live_trading_guardrails", "kraken_validation_only", "requires"),
    ("live_trading_guardrails", "low_funds_alerts", "recommends"),
    ("kraken_validation_only", "orders_jsonl", "records"),
    ("low_funds_alerts", "railway_state_volume", "stores_state"),
    ("local_vs_remote_state", "railway_state_volume", "explains"),
    ("local_vs_remote_state", "strategy_yaml", "explains"),
    ("operations", "heartbeat_json", "checks"),
    ("operations", "orders_jsonl", "checks"),
    ("operations", "discord_notifications", "configures"),
]


DOC_NODE_IDS = {
    key: f"doc_{key}"
    for key in DOCS
}


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def first_headings(text: str, limit: int = 6) -> list[str]:
    headings = []
    for line in text.splitlines():
        if line.startswith("#"):
            headings.append(line.strip("# ").strip())
        if len(headings) >= limit:
            break
    return headings


def node_link_graph() -> dict:
    nodes = []
    links = []
    captured_at = datetime.now(timezone.utc).isoformat()

    for key, relative_path in DOCS.items():
        text = read_text(relative_path)
        nodes.append(
            {
                "id": DOC_NODE_IDS[key],
                "label": Path(relative_path).name,
                "file_type": "document",
                "source_file": str(ROOT / relative_path),
                "source_location": None,
                "source_url": None,
                "captured_at": captured_at,
                "author": None,
                "contributor": "Codex",
                "headings": first_headings(text),
            }
        )

    for concept in CONCEPTS:
        nodes.append(
            {
                "id": concept["id"],
                "label": concept["label"],
                "file_type": "concept",
                "source_file": str(ROOT / concept["source"]),
                "source_location": None,
                "source_url": None,
                "captured_at": captured_at,
                "author": None,
                "contributor": "Codex",
                "rationale": concept["summary"],
                "tags": concept["tags"],
            }
        )
        source_key = next(key for key, path in DOCS.items() if path == concept["source"])
        links.append(
            {
                "source": DOC_NODE_IDS[source_key],
                "target": concept["id"],
                "relation": "documents",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": str(ROOT / concept["source"]),
                "source_location": None,
                "weight": 1.0,
            }
        )

    for source, target, relation in EDGES:
        if source == "operations":
            source = DOC_NODE_IDS["operations"]
        source_file = next((str(ROOT / c["source"]) for c in CONCEPTS if c["id"] == source), None)
        links.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "confidence": "INFERRED" if relation in {"explains", "limits"} else "EXTRACTED",
                "confidence_score": 0.85 if relation in {"explains", "limits"} else 1.0,
                "source_file": source_file or str(ROOT / "README.md"),
                "source_location": None,
                "weight": 1.0,
            }
        )

    return {
        "directed": True,
        "multigraph": False,
        "graph": {
            "mode": "documentation-reference",
            "generated_by": "scripts/build_docs_graph.py",
            "generated_at": captured_at,
            "root": str(ROOT),
        },
        "nodes": nodes,
        "links": links,
        "hyperedges": [
            {
                "id": "runtime_observability_loop",
                "label": "Runtime Observability Loop",
                "nodes": ["heartbeat_json", "decisions_jsonl", "trades_jsonl", "orders_jsonl", "hermes_flow"],
                "relation": "form",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": str(ROOT / "docs/OPERATIONS.md"),
            },
            {
                "id": "safe_training_surface",
                "label": "Safe Training Surface",
                "nodes": ["multi_asset_training", "kraken_market_data", "kraken_validation_only", "live_trading_guardrails"],
                "relation": "form",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": str(ROOT / "docs/SAFETY.md"),
            },
        ],
    }


def obsidian_name(label: str) -> str:
    if label.lower().endswith(".md"):
        label = label[:-3]
    return label.replace("/", "-").replace(":", " -")


def write_obsidian_notes(graph: dict) -> None:
    OBSIDIAN_NOTES.mkdir(parents=True, exist_ok=True)
    (OBSIDIAN_OUT / ".obsidian").mkdir(parents=True, exist_ok=True)
    for old_note in OBSIDIAN_NOTES.glob("*.md"):
        old_note.unlink()

    node_by_id = {node["id"]: node for node in graph["nodes"]}
    outgoing: dict[str, list[dict]] = {node["id"]: [] for node in graph["nodes"]}
    incoming: dict[str, list[dict]] = {node["id"]: [] for node in graph["nodes"]}
    for link in graph["links"]:
        outgoing.setdefault(link["source"], []).append(link)
        incoming.setdefault(link["target"], []).append(link)

    name_by_id = {node["id"]: obsidian_name(node["label"]) for node in graph["nodes"]}

    for node in graph["nodes"]:
        source_file = Path(node["source_file"])
        lines = [
            "---",
            f"graphify_id: {node['id']}",
            f"file_type: {node['file_type']}",
            "project: Hermes Trading",
            "---",
            "",
            f"# {node['label']}",
            "",
        ]
        if node.get("rationale"):
            lines += [node["rationale"], ""]
        if node.get("headings"):
            lines += ["## Sections sources", ""]
            lines += [f"- {heading}" for heading in node["headings"]]
            lines.append("")
        lines += [
            "## Source",
            "",
            f"- [{source_file.name}]({source_file.as_posix()})",
            "",
        ]
        if outgoing.get(node["id"]):
            lines += ["## Liens sortants", ""]
            for link in outgoing[node["id"]]:
                target = node_by_id[link["target"]]
                lines.append(f"- {link['relation']} -> [[{name_by_id[target['id']]}]]")
            lines.append("")
        if incoming.get(node["id"]):
            lines += ["## Liens entrants", ""]
            for link in incoming[node["id"]]:
                source = node_by_id[link["source"]]
                lines.append(f"- [[{name_by_id[source['id']]}]] -> {link['relation']}")
            lines.append("")
        lines += ["## Tags", ""]
        tags = node.get("tags", [])
        if tags:
            lines += [f"- #{tag}" for tag in tags]
        else:
            lines.append("- #documentation")
        lines.append("")
        (OBSIDIAN_NOTES / f"{name_by_id[node['id']]}.md").write_text("\n".join(lines), encoding="utf-8")

    index_lines = [
        "# Hermes Trading Graph",
        "",
        "Carte Obsidian generee depuis les documents locaux Hermes Trading.",
        "",
        "## Concepts principaux",
        "",
    ]
    for concept in CONCEPTS:
        index_lines.append(f"- [[{name_by_id[concept['id']]}]]")
    index_lines += [
        "",
        "## Documents sources",
        "",
    ]
    for key in DOCS:
        index_lines.append(f"- [[{name_by_id[DOC_NODE_IDS[key]]}]]")
    index_lines += [
        "",
        "## Canvas",
        "",
        "- [[graph.canvas]]",
        "",
    ]
    (OBSIDIAN_OUT / "Hermes Trading Graph.md").write_text("\n".join(index_lines), encoding="utf-8")


def write_canvas(graph: dict) -> None:
    nodes = []
    edges = []
    positions = {
        "hermes_trading_worker": (0, 0),
        "multi_asset_training": (420, 0),
        "kraken_market_data": (420, -240),
        "railway_deployment": (-420, -240),
        "railway_state_volume": (-420, 0),
        "hermes_flow": (-420, 240),
        "discord_notifications": (-840, 480),
        "pnl_summary_notifications": (-840, 720),
        "low_funds_alerts": (-1260, 720),
        "strategy_yaml": (0, 240),
        "goal_yaml": (420, 240),
        "decisions_jsonl": (0, 480),
        "trades_jsonl": (420, 480),
        "orders_jsonl": (840, 480),
        "heartbeat_json": (-420, 480),
        "no_trade_reflection": (-840, 240),
        "live_trading_guardrails": (840, -240),
        "kraken_validation_only": (840, 0),
        "local_vs_remote_state": (-840, 0),
    }
    for concept in CONCEPTS:
        x, y = positions[concept["id"]]
        nodes.append(
            {
                "id": concept["id"],
                "type": "file",
                "file": f"notes/{obsidian_name(concept['label'])}.md",
                "x": x,
                "y": y,
                "width": 320,
                "height": 180,
            }
        )
    for idx, (source, target, relation) in enumerate(EDGES):
        if source == "operations":
            continue
        if source in positions and target in positions:
            edges.append(
                {
                    "id": f"edge_{idx:02d}",
                    "fromNode": source,
                    "fromSide": "right",
                    "toNode": target,
                    "toSide": "left",
                    "label": relation,
                }
            )
    (OBSIDIAN_OUT / "graph.canvas").write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=2), encoding="utf-8")


def write_html(graph: dict) -> None:
    node_items = "\n".join(f"<li><strong>{n['label']}</strong> <code>{n['id']}</code></li>" for n in graph["nodes"])
    edge_items = "\n".join(
        f"<li><code>{e['source']}</code> - {e['relation']} -> <code>{e['target']}</code></li>"
        for e in graph["links"]
    )
    html = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Hermes Trading Graph</title>
  <style>
    body {{ font-family: Segoe UI, sans-serif; margin: 32px; line-height: 1.5; }}
    code {{ background: #f4f4f4; padding: 2px 4px; border-radius: 4px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>Hermes Trading Graph</h1>
  <p>Graphe documentaire local genere depuis README.md, docs/ et state/.</p>
  <div class="grid">
    <section><h2>Noeuds</h2><ul>{node_items}</ul></section>
    <section><h2>Relations</h2><ul>{edge_items}</ul></section>
  </div>
</body>
</html>
"""
    (GRAPHIFY_OUT / "graph.html").write_text(html, encoding="utf-8")


def write_report(graph: dict) -> None:
    concepts = [node for node in graph["nodes"] if node["file_type"] == "concept"]
    documents = [node for node in graph["nodes"] if node["file_type"] == "document"]
    report = [
        f"# Graph Report - {ROOT} ({datetime.now().date().isoformat()})",
        "",
        "## Corpus Check",
        "",
        "- documentation-reference mode",
        f"- Documentation files: {len(documents)}",
        f"- Concept nodes: {len(concepts)}",
        f"- Reference edges: {len(graph['links'])}",
        "",
        "## God Nodes",
        "",
        "- Hermes Trading Worker: central runtime component connecting market data, strategy, state and observability.",
        "- Railway State Volume: operational source of truth for the deployed worker.",
        "- Hermes Flow: reflection loop that reads decisions/trades and proposes strategy changes.",
        "",
        "## Surprising Connections",
        "",
        "- No-trade decisions make an empty trades journal useful: they let Hermes detect a strategy that is too strict.",
        "- A stale local strategy.yaml is not necessarily a worker failure; the Railway volume can be newer than local state.",
        "- Kraken validation-only makes the first live step observable without placing real orders.",
        "",
        "## Suggested Questions",
        "",
        "- Why can strategy.yaml look stale locally while the worker is healthy?",
        "- Which files prove the multi-asset training loop is alive?",
        "- What must change before live Kraken trading is possible?",
        "",
        "## Documentation Nodes",
        "",
    ]
    report += [f"- {node['label']} ({node['id']})" for node in documents]
    report += ["", "## Concept Nodes", ""]
    report += [f"- {node['label']} ({node['id']})" for node in concepts]
    (GRAPHIFY_OUT / "GRAPH_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def write_project_index() -> None:
    index = ROOT / "docs" / "GRAPHIFY_OBSIDIAN.md"
    text = """# Graphify et Obsidian

Cette page relie la documentation Hermes Trading aux sorties locales Graphify et Obsidian.

## Entrees principales

- [Rapport Graphify](../graphify-out/GRAPH_REPORT.md)
- [Graphe JSON](../graphify-out/graph.json)
- [Vue HTML](../graphify-out/graph.html)
- [Index Obsidian](../graphify-out/obsidian/Hermes%20Trading%20Graph.md)
- [Canvas Obsidian](../graphify-out/obsidian/graph.canvas)

## Notes source

- [[ARCHITECTURE]]
- [[OPERATIONS]]
- [[HERMES]]
- [[KRAKEN]]
- [[DEPLOYMENT]]
- [[SAFETY]]
- [[STATE_FILES]]
- [[TROUBLESHOOTING]]
- [[COMMANDS]]

## Usage

Ouvrir `graphify-out/obsidian/Hermes Trading Graph.md` dans Obsidian pour naviguer par concepts, ou `graphify-out/obsidian/graph.canvas` pour voir le flux visuel.
"""
    index.write_text(text, encoding="utf-8")


def main() -> None:
    GRAPHIFY_OUT.mkdir(exist_ok=True)
    graph = node_link_graph()
    (GRAPHIFY_OUT / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    write_obsidian_notes(graph)
    write_canvas(graph)
    write_html(graph)
    write_report(graph)
    write_project_index()
    manifest = {
        "generated_by": "scripts/build_docs_graph.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_files": [str(ROOT / path) for path in DOCS.values()],
        "outputs": [
            str(GRAPHIFY_OUT / "graph.json"),
            str(GRAPHIFY_OUT / "GRAPH_REPORT.md"),
            str(GRAPHIFY_OUT / "graph.html"),
            str(OBSIDIAN_OUT / "Hermes Trading Graph.md"),
            str(OBSIDIAN_OUT / "graph.canvas"),
        ],
    }
    (GRAPHIFY_OUT / "docs-graph-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {len(graph['nodes'])} nodes and {len(graph['links'])} edges")


if __name__ == "__main__":
    main()
