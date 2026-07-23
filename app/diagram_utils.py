"""
人物相関図の描画用データ変換ユーティリティ

AIが出した構造化データ（JSON）を、そのままAIに図の記法まで書かせるのではなく、
アプリ側のコードで機械的に描画ライブラリ用のデータへ変換する。
AIに直接図の記法まで書かせると文法ミスで図が壊れることがあるため、
「データ生成はAI、描画用データへの変換はコード」という役割分担にしている。

描画にはvis.js（vis-network）を使用する。Mermaid.jsと違い、利用者がノードを
ドラッグして自由に配置し直せる（レイアウトを操作できる）ため、
ノード数が多く見にくくなりがちな相関図に向いている。
"""
from __future__ import annotations


def _clean_label(text: str) -> str:
    """表示用ラベルとして整形する（改行を除去する程度の軽い整形のみ）"""
    return (text or "").strip().replace("\n", " ")


def build_vis_network_data(relationships: list[dict], characters=None) -> dict:
    """人物相関図データ（[{"from","to","relationship"}, ...]）から、
    vis-network用の {"nodes": [...], "edges": [...]} データを組み立てる。

    characters（Characterモデルのリスト）を渡すと、AIが返した名前と登録済みの
    キャラクター名を突き合わせて、サムネイル画像が設定されているキャラクターは
    ノードを円形の顔写真として表示する（未設定・名前が一致しない場合は通常の
    ボックス表示のまま）。
    """
    name_to_character = {}
    for c in characters or []:
        if c.thumbnail_filename:
            name_to_character[c.name.strip().lower()] = c

    node_ids: dict[str, int] = {}

    def get_node_id(name: str) -> int:
        if name not in node_ids:
            node_ids[name] = len(node_ids) + 1
        return node_ids[name]

    edges = []
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        frm = _clean_label(str(rel.get("from") or ""))
        to = _clean_label(str(rel.get("to") or ""))
        label = _clean_label(str(rel.get("relationship") or ""))
        if not frm or not to:
            continue
        edges.append({"from": get_node_id(frm), "to": get_node_id(to), "label": label})

    nodes = []
    for name, node_id in node_ids.items():
        node = {"id": node_id, "label": name}
        character = name_to_character.get(name.strip().lower())
        if character is not None:
            node["shape"] = "circularImage"
            node["image"] = f"/projects/{character.project_id}/characters/{character.id}/thumbnail"
        nodes.append(node)

    return {"nodes": nodes, "edges": edges}
