"""Custom Copilot tools exposing Shotwright container and project controls."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from copilot.tools import Tool, ToolInvocation, ToolResult

from app.database import get_message_collection, get_session_collection
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services import reference_media as rm
from app.services.session_streams import publish_context_refresh, publish_session_updated
from app.services.video_streaming import generate_hls

_REFERENCE_ASSET_DIRECTORY = Path("assets") / "references"


def _tool_success(payload: dict, session_log: str) -> ToolResult:
    return ToolResult(
        text_result_for_llm=_serialize_tool_payload(payload),
        result_type="success",
        session_log=session_log,
    )


def _tool_failure(message: str, *, error: str | None = None) -> ToolResult:
    return ToolResult(
        text_result_for_llm=message,
        result_type="failure",
        error=error or message,
    )


def _normalize_tool_payload_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize_tool_payload_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_tool_payload_value(item) for item in value]
    return str(value)


def _serialize_tool_payload(payload: dict) -> str:
    return json.dumps(_normalize_tool_payload_value(payload), ensure_ascii=False)


def _sanitize_asset_file_name(value: str | None, fallback_stem: str, suffix: str) -> str:
    raw_name = Path(value).name if value else ""
    raw_stem = Path(raw_name).stem.strip() if raw_name else fallback_stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', raw_stem).strip().strip('.') or fallback_stem

    resolved_suffix = Path(raw_name).suffix.lower() if raw_name else suffix.lower()
    if resolved_suffix and not resolved_suffix.startswith('.'):
        resolved_suffix = f'.{resolved_suffix}'

    return f"{safe_stem}{resolved_suffix or suffix}"


def _jsx_string(value: str) -> str:
    return json.dumps(Path(value).as_posix())


def _should_reuse_generated_project_workspace(project: dict | None) -> bool:
    if not isinstance(project, dict):
        return False

    if str(project.get("origin") or "").strip().lower() != "generated":
        return False

    workspace_dir = str(project.get("workspace_dir") or "").strip()
    entry_aep_file = str(project.get("entry_aep_file") or project.get("filename") or "").strip()
    if not workspace_dir or not entry_aep_file:
        return False

    aep_files = [str(path).strip() for path in (project.get("aep_files") or []) if str(path).strip()]
    return not aep_files and Path(workspace_dir).exists()


def _build_safe_after_effects_demo_script() -> str:
    return r"""
(function () {
    app.beginUndoGroup("Shotwright safe motion demo");
    var proj = app.project;
    if (!proj) { app.newProject(); proj = app.project; }
    while (proj.items.length > 0) {
        try { proj.items[1].remove(); } catch (clearError) { break; }
    }

    var W = 1920, H = 1080, FPS = 30, DUR = 7;
    var comp = proj.items.addComp("Main", W, H, 1, DUR, FPS);
    comp.bgColor = [0.002, 0.003, 0.012];
    comp.motionBlur = true;
    comp.shutterAngle = 180;

    function p(group, name) { try { return group ? group.property(name) : null; } catch (error) { return null; } }
    function t(layer) { return p(layer, "ADBE Transform Group"); }
    function setv(prop, value) { try { if (prop) { prop.setValue(value); } } catch (error) {} }
    function key(prop, time, value) { try { if (prop) { prop.setValueAtTime(time, value); } } catch (error) {} }
    function set2d(layer, x, y) { setv(p(t(layer), "ADBE Position"), [x, y]); }
    function set3d(layer, x, y, z) { layer.threeDLayer = true; setv(p(t(layer), "ADBE Position"), [x, y, z]); }
    function root(layer) { return p(layer, "ADBE Root Vectors Group"); }
    function glow(layer, radius, intensity) {
        try {
            var fx = p(layer, "ADBE Effect Parade").addProperty("ADBE Glow");
            setv(p(fx, "ADBE Glow-0002"), radius);
            setv(p(fx, "ADBE Glow-0003"), intensity);
        } catch (error) {}
    }
    function addPath(layer, name, points, color, width, opacity) {
        try {
            var group = root(layer).addProperty("ADBE Vector Group");
            group.name = name;
            var contents = p(group, "ADBE Vectors Group");
            var shapeGroup = contents.addProperty("ADBE Vector Shape - Group");
            var shape = new Shape();
            shape.vertices = points;
            shape.inTangents = [];
            shape.outTangents = [];
            for (var i = 0; i < points.length; i += 1) { shape.inTangents.push([0, 0]); shape.outTangents.push([0, 0]); }
            shape.closed = false;
            p(shapeGroup, "ADBE Vector Shape").setValue(shape);
            var stroke = contents.addProperty("ADBE Vector Graphic - Stroke");
            setv(p(stroke, "ADBE Vector Stroke Color"), color);
            setv(p(stroke, "ADBE Vector Stroke Width"), width);
            setv(p(stroke, "ADBE Vector Stroke Opacity"), opacity);
            return contents;
        } catch (error) { return null; }
    }
    function addEllipse(layer, name, position, size, color, opacity, strokeWidth) {
        try {
            var group = root(layer).addProperty("ADBE Vector Group");
            group.name = name;
            var contents = p(group, "ADBE Vectors Group");
            var ellipse = contents.addProperty("ADBE Vector Shape - Ellipse");
            setv(p(ellipse, "ADBE Vector Ellipse Position"), position);
            setv(p(ellipse, "ADBE Vector Ellipse Size"), size);
            if (strokeWidth && strokeWidth > 0) {
                var stroke = contents.addProperty("ADBE Vector Graphic - Stroke");
                setv(p(stroke, "ADBE Vector Stroke Color"), color);
                setv(p(stroke, "ADBE Vector Stroke Width"), strokeWidth);
                setv(p(stroke, "ADBE Vector Stroke Opacity"), opacity);
            } else {
                var fill = contents.addProperty("ADBE Vector Graphic - Fill");
                setv(p(fill, "ADBE Vector Fill Color"), color);
                setv(p(fill, "ADBE Vector Fill Opacity"), opacity);
            }
            return contents;
        } catch (error) { return null; }
    }

    var bg = comp.layers.addSolid([0.002, 0.003, 0.012], "Deep navy stage base", W, H, 1, DUR);
    bg.moveToEnd();

    function starLayer(name, count, z, seed, color, size, drift) {
        var layer = comp.layers.addShape();
        layer.name = name;
        set3d(layer, 0, 0, z);
        for (var i = 0; i < count; i += 1) {
            var x = Math.sin((i + 1) * (12.9898 + seed)) * 43758.5453;
            var y = Math.sin((i + 1) * (78.233 + seed)) * 24634.6345;
            x = (x - Math.floor(x)) * W;
            y = (y - Math.floor(y)) * H;
            var s = size * (0.45 + ((i * 37) % 100) / 100);
            addEllipse(layer, "star " + i, [x, y], [s, s], color, 35 + ((i * 11) % 55), 0);
        }
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*0.21+" + seed + ")*" + drift + ", Math.cos(time*0.13+" + seed + ")*" + (drift * 0.5) + ", 0];";
        glow(layer, 24, 0.8);
    }
    starLayer("Far cyan stardust parallax", 72, 900, 1.3, [0.25, 0.8, 1], 4, 18);
    starLayer("Mid magenta stardust parallax", 58, 430, 3.7, [1, 0.25, 0.9], 5, 32);
    starLayer("Near white spark dust", 38, -60, 5.2, [0.85, 1, 0.95], 6, 52);

    var grid = comp.layers.addShape();
    grid.name = "Neon perspective floor grid";
    set3d(grid, 0, 0, 260);
    for (var gy = 1; gy <= 12; gy += 1) {
        var f = gy / 12;
        var y = 540 + Math.pow(f, 1.7) * 620;
        var half = 90 + f * 1240;
        addPath(grid, "horizon row " + gy, [[960 - half, y], [960 + half, y]], [0, 0.8, 1], 2.2, 58);
    }
    for (var gx = -520; gx <= 2440; gx += 160) {
        addPath(grid, "vanish ray " + gx, [[960, 430], [gx, 1240]], [0.7, 0.1, 1], 1.6, 46);
    }
    p(t(grid), "ADBE Position").expression = "value + [0, (time*24)%55, 0];";
    glow(grid, 36, 1.1);

    function orbit(name, size, color, z, rx, ry, rz, speed) {
        var layer = comp.layers.addShape();
        layer.name = name;
        set3d(layer, W / 2, H / 2, z);
        var contents = addEllipse(layer, "orbit stroke", [0, 0], size, color, 88, 4);
        try {
            var trim = contents.addProperty("ADBE Vector Filter - Trim");
            p(trim, "ADBE Vector Trim End").setValue(72);
            p(trim, "ADBE Vector Trim Offset").expression = "time*" + speed;
        } catch (trimError) {}
        setv(p(t(layer), "ADBE Rotate X"), rx);
        setv(p(t(layer), "ADBE Rotate Y"), ry);
        setv(p(t(layer), "ADBE Rotate Z"), rz);
        p(t(layer), "ADBE Rotate Z").expression = "value + time*" + (speed * 0.12);
        layer.motionBlur = true;
        glow(layer, 48, 1.4);
    }
    orbit("Cyan orbit ring A", [650, 176], [0, 0.95, 1], -160, 68, 0, 0, 92);
    orbit("Magenta orbit ring B", [520, 315], [1, 0.15, 0.88], -120, 54, 24, 16, -116);
    orbit("Lime orbit ring C", [780, 120], [0.4, 1, 0.55], -210, 76, -20, -12, 156);

    var hud = comp.layers.addShape();
    hud.name = "HUD targeting frame";
    set3d(hud, W / 2, H / 2, -130);
    addPath(hud, "corner tl a", [[-380, -165], [-298, -165]], [0, 0.9, 1], 3, 78);
    addPath(hud, "corner tl b", [[-380, -165], [-380, -83]], [0, 0.9, 1], 3, 78);
    addPath(hud, "corner br a", [[380, 165], [298, 165]], [1, 0.15, 0.9], 3, 78);
    addPath(hud, "corner br b", [[380, 165], [380, 83]], [1, 0.15, 0.9], 3, 78);
    addPath(hud, "cross x", [[-78, 0], [78, 0]], [0.65, 1, 0.86], 1.5, 65);
    addPath(hud, "cross y", [[0, -78], [0, 78]], [0.65, 1, 0.86], 1.5, 65);
    addEllipse(hud, "inner reticle", [0, 0], [124, 124], [0.45, 1, 0.9], 62, 2);
    p(t(hud), "ADBE Rotate Z").expression = "Math.sin(time*1.3)*2.5";
    glow(hud, 38, 1.2);

    function textDoc(layer, size, color, tracking) {
        var docProp = p(p(layer, "ADBE Text Properties"), "ADBE Text Document");
        var doc = docProp.value;
        doc.fontSize = size;
        doc.fillColor = color;
        doc.justification = ParagraphJustification.CENTER_JUSTIFY;
        try { doc.tracking = tracking; } catch (trackingError) {}
        docProp.setValue(doc);
        return docProp;
    }
    function revealText(label, start, y, size, color, tracking) {
        var layer = comp.layers.addText(label);
        layer.name = "Type reveal " + label;
        set3d(layer, W / 2, y, -120);
        var docProp = textDoc(layer, size, color, tracking);
        docProp.expression = "var full='" + label + "'; var c=Math.floor(linear(time," + start + "," + (start + 0.82) + ",0,full.length)); c=Math.max(0,Math.min(full.length,c)); full.substr(0,c);";
        key(p(t(layer), "ADBE Opacity"), start - 0.05, 0);
        key(p(t(layer), "ADBE Opacity"), start + 0.16, 100);
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*17+index)*5, Math.sin(time*23+index)*2, 0];";
        layer.motionBlur = true;
        glow(layer, 58, 1.5);
    }
    revealText("SHOTWRIGHT", 0.35, 240, 118, [0.55, 0.95, 1], 150);
    revealText("CODEX", 1.12, 350, 104, [1, 0.28, 0.95], 205);
    revealText("AE SKILL", 1.82, 456, 76, [0.42, 1, 0.72], 130);

    function countText(label, t0, t1, size) {
        var layer = comp.layers.addText(label);
        layer.name = "Countdown " + label;
        set3d(layer, W / 2, 620, -145);
        textDoc(layer, size, label === "LAUNCH" ? [0.95, 1, 0.72] : [0.72, 0.98, 1], label === "LAUNCH" ? 70 : 20);
        key(p(t(layer), "ADBE Opacity"), t0 - 0.04, 0);
        key(p(t(layer), "ADBE Opacity"), t0 + 0.08, 100);
        key(p(t(layer), "ADBE Opacity"), t1 - 0.12, 100);
        key(p(t(layer), "ADBE Opacity"), t1, 0);
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*19+index)*4, Math.cos(time*29+index)*2, 0];";
        glow(layer, 64, 1.7);
    }
    countText("03", 0.52, 2.0, 218);
    countText("02", 2.0, 3.5, 218);
    countText("01", 3.5, 5.0, 218);
    countText("LAUNCH", 5.0, 7.0, 132);

    function burst(time, index, color) {
        var wave = comp.layers.addShape();
        wave.name = "Radial shockwave " + index;
        set3d(wave, W / 2, H / 2, -105);
        addEllipse(wave, "impact wave", [0, 0], [420, 420], color, 95, 4);
        key(p(t(wave), "ADBE Scale"), time, [18, 18, 18]);
        key(p(t(wave), "ADBE Scale"), time + 0.5, [190, 190, 190]);
        key(p(t(wave), "ADBE Opacity"), time, 92);
        key(p(t(wave), "ADBE Opacity"), time + 0.5, 0);
        glow(wave, 70, 1.6);
        var scan = comp.layers.addShape();
        scan.name = "Scanline burst " + index;
        for (var sy = 0; sy < H; sy += 28) {
            addPath(scan, "scan " + sy, [[0, sy], [W, sy]], color, 1.2, 32);
        }
        key(p(t(scan), "ADBE Opacity"), time - 0.03, 0);
        key(p(t(scan), "ADBE Opacity"), time + 0.03, 70);
        key(p(t(scan), "ADBE Opacity"), time + 0.34, 0);
        glow(scan, 32, 1.1);
    }
    burst(0.52, 1, [0, 0.9, 1]);
    burst(2.0, 2, [1, 0.12, 0.9]);
    burst(3.5, 3, [0.45, 1, 0.55]);
    burst(5.0, 4, [1, 0.9, 0.25]);

    var cam = comp.layers.addCamera("Drift push camera", [W / 2, H / 2]);
    key(p(t(cam), "ADBE Position"), 0, [940, 548, -1740]);
    key(p(t(cam), "ADBE Position"), DUR, [1040, 526, -1240]);
    key(p(t(cam), "ADBE Point of Interest"), 0, [960, 545, 0]);
    key(p(t(cam), "ADBE Point of Interest"), DUR, [960, 560, 80]);
    p(t(cam), "ADBE Position").expression = "value + [Math.sin(time*0.73)*18, Math.sin(time*0.41)*7, 0];";
    try { p(p(cam, "ADBE Camera Options Group"), "ADBE Camera Zoom").setValue(1450); } catch (zoomError) {}

    comp.openInViewer();
    var savePath = $.getenv("SHOTWRIGHT_PROJECT_FILE");
    if (savePath) { proj.save(new File(savePath)); }
    app.endUndoGroup();
}());
""".strip()


async def _list_session_image_attachments(session_id: str, *, limit: int = 8) -> list[dict]:
    attachments: list[dict] = []
    seen_paths: set[str] = set()

    cursor = get_message_collection().find(
        {"session_id": session_id},
        {"metadata.attachments": 1, "created_at": 1},
    ).sort("created_at", -1)

    async for message_doc in cursor:
        metadata = message_doc.get("metadata") or {}
        for attachment in metadata.get("attachments") or []:
            if not isinstance(attachment, dict) or attachment.get("type") != "image":
                continue

            file_path = str(attachment.get("file_path") or "").strip()
            if not file_path:
                continue

            resolved_path = Path(file_path)
            if not resolved_path.exists():
                continue

            dedupe_key = str(resolved_path).lower()
            if dedupe_key in seen_paths:
                continue
            seen_paths.add(dedupe_key)

            attachments.append(
                {
                    "file_path": str(resolved_path),
                    "display_name": attachment.get("display_name") or resolved_path.name,
                    "mime_type": attachment.get("mime_type"),
                    "shared_relative_path": attachment.get("shared_relative_path"),
                    "workspace_relative_path": attachment.get("workspace_relative_path"),
                    "width": attachment.get("width"),
                    "height": attachment.get("height"),
                    "size_bytes": attachment.get("size_bytes"),
                    "created_at": message_doc.get("created_at"),
                }
            )
            if len(attachments) >= limit:
                return attachments

    return attachments


async def list_session_image_attachments(session_id: str, *, limit: int = 8) -> list[dict]:
    return await _list_session_image_attachments(session_id, limit=limit)


def _copy_asset_into_project(
    project: dict,
    source_path: Path,
    *,
    display_name: str | None = None,
    asset_name: str | None = None,
    target_directory: Path = _REFERENCE_ASSET_DIRECTORY,
) -> dict:
    if not source_path.exists():
        raise FileNotFoundError(f"Reference asset not found at {source_path}")

    project_root = Path(project["workspace_dir"])
    destination_dir = project_root / target_directory
    destination_dir.mkdir(parents=True, exist_ok=True)

    suffix = source_path.suffix.lower() or ".bin"
    destination_name = _sanitize_asset_file_name(asset_name or display_name, "reference-image", suffix)
    destination_path = destination_dir / destination_name

    if str(source_path.resolve()).lower() != str(destination_path.resolve()).lower():
        shutil.copy2(source_path, destination_path)

    return {
        "source_path": str(source_path),
        "project_asset_path": str(destination_path),
        "project_relative_path": destination_path.relative_to(project_root).as_posix(),
        "display_name": display_name or source_path.name,
    }


async def _stage_session_image_attachments(
    session_id: str,
    project: dict,
    *,
    latest_only: bool = True,
    asset_name: str | None = None,
) -> list[dict]:
    image_attachments = await _list_session_image_attachments(session_id, limit=1 if latest_only else 8)
    if not image_attachments:
        return []

    staged_assets: list[dict] = []
    total = len(image_attachments)
    for index, attachment in enumerate(image_attachments, start=1):
        source_path = Path(str(attachment["file_path"]))
        desired_name = asset_name
        if not latest_only and total > 1 and desired_name:
            suffix = source_path.suffix.lower() or ".bin"
            desired_name = f"{Path(desired_name).stem}-{index:02d}{suffix}"

        staged_assets.append(
            _copy_asset_into_project(
                project,
                source_path,
                display_name=str(attachment.get("display_name") or source_path.name),
                asset_name=desired_name,
            )
        )

    return staged_assets


def _build_empty_project_jsx() -> str:
    return "\n".join(
        [
            "app.beginSuppressDialogs();",
            "if (typeof CloseOptions !== \"undefined\" && app.project && typeof app.project.close === \"function\") {",
            "    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);",
            "}",
        ]
    )


def _build_reference_composition_jsx(
    *,
    reference_asset_path: str,
    composition_name: str,
    width: int,
    height: int,
    duration_seconds: float,
    frame_rate: float,
    fit_mode: str,
    reset_existing: bool,
) -> str:
    normalized_fit_mode = "contain" if fit_mode == "contain" else "cover"

    return "\n".join(
        [
            "app.beginSuppressDialogs();",
            "function normalizePath(value) {",
            "    if (!value) { return \"\"; }",
            "    return value.toString().replace(/\\\\/g, \"/\").toLowerCase();",
            "}",
            "function findCompByName(name) {",
            "    if (!app.project) { return null; }",
            "    for (var itemIndex = 1; itemIndex <= app.project.items.length; itemIndex += 1) {",
            "        var item = app.project.items[itemIndex];",
            "        if (item instanceof CompItem && item.name === name) {",
            "            return item;",
            "        }",
            "    }",
            "    return null;",
            "}",
            "function findFootageByPath(targetPath) {",
            "    var normalizedTargetPath = normalizePath(targetPath);",
            "    if (!normalizedTargetPath || !app.project) { return null; }",
            "    for (var itemIndex = 1; itemIndex <= app.project.items.length; itemIndex += 1) {",
            "        var item = app.project.items[itemIndex];",
            "        if (!(item instanceof FootageItem) || !item.file) { continue; }",
            "        if (normalizePath(item.file.fsName) === normalizedTargetPath) {",
            "            return item;",
            "        }",
            "    }",
            "    return null;",
            "}",
            "function removeLayerByName(comp, name) {",
            "    if (!comp) { return; }",
            "    for (var layerIndex = comp.numLayers; layerIndex >= 1; layerIndex -= 1) {",
            "        var layer = comp.layer(layerIndex);",
            "        if (layer && layer.name === name) {",
            "            layer.remove();",
            "        }",
            "    }",
            "}",
            "function fitLayerToComp(layer, comp, mode) {",
            "    if (!layer || !layer.source || !comp) { return; }",
            "    var sourceWidth = layer.source.width || comp.width;",
            "    var sourceHeight = layer.source.height || comp.height;",
            "    if (!sourceWidth || !sourceHeight) { return; }",
            "    var scaleX = (comp.width / sourceWidth) * 100;",
            "    var scaleY = (comp.height / sourceHeight) * 100;",
            "    var uniformScale = mode === \"contain\" ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY);",
            "    try { layer.property(\"Anchor Point\").setValue([sourceWidth / 2, sourceHeight / 2]); } catch (anchorError) {}",
            "    layer.property(\"Scale\").setValue([uniformScale, uniformScale]);",
            "    layer.property(\"Position\").setValue([comp.width / 2, comp.height / 2]);",
            "}",
            f"var referenceFile = new File({_jsx_string(reference_asset_path)});",
            "if (!referenceFile.exists) { throw new Error(\"Reference image not found: \" + referenceFile.fsName); }",
            f"var compositionName = {json.dumps(composition_name)};",
            f"var fitMode = {json.dumps(normalized_fit_mode)};",
            f"var resetExisting = {'true' if reset_existing else 'false'};",
            "var footage = findFootageByPath(referenceFile.fsName);",
            "if (!footage) {",
            "    footage = app.project.importFile(new ImportOptions(referenceFile));",
            "}",
            "var comp = findCompByName(compositionName);",
            "if (!comp) {",
            f"    comp = app.project.items.addComp(compositionName, {max(16, int(width))}, {max(16, int(height))}, 1, {max(1.0, float(duration_seconds))}, {max(1.0, float(frame_rate))});",
            "} else {",
            f"    comp.width = {max(16, int(width))};",
            f"    comp.height = {max(16, int(height))};",
            f"    comp.duration = {max(1.0, float(duration_seconds))};",
            f"    comp.frameRate = {max(1.0, float(frame_rate))};",
            "}",
            "if (resetExisting) {",
            "    for (var layerIndex = comp.numLayers; layerIndex >= 1; layerIndex -= 1) {",
            "        comp.layer(layerIndex).remove();",
            "    }",
            "} else {",
            "    removeLayerByName(comp, \"shotwright_reference_image\");",
            "}",
            "var imageLayer = comp.layers.add(footage);",
            "imageLayer.name = \"shotwright_reference_image\";",
            "fitLayerToComp(imageLayer, comp, fitMode);",
            "comp.openInViewer();",
        ]
    )


def build_shotwright_tools(app_session_id: str) -> list[Tool]:
    """Build session-scoped tools for the Copilot runtime."""

    def _coerce_timeout_seconds(raw_value: object, default: int = 300) -> int:
        if raw_value is None:
            return default
        try:
            return max(30, int(raw_value))
        except (TypeError, ValueError):
            return default

    def _coerce_bool(raw_value: object, default: bool = False) -> bool:
        if raw_value is None:
            return default
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(raw_value)

    async def _create_project_from_script(
        *,
        arguments: dict,
        script_content: str,
        default_description: str,
    ) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project = None
        reused_existing_workspace = False
        candidate_project_ids: list[str] = []

        requested_project_id = str(arguments.get("project_id") or "").strip()
        if requested_project_id:
            candidate_project_ids.append(requested_project_id)

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if active_project_id and active_project_id not in candidate_project_ids:
            candidate_project_ids.append(active_project_id)

        for candidate_project_id in candidate_project_ids:
            candidate_project = await pm.get_project(app_session_id, candidate_project_id)
            if _should_reuse_generated_project_workspace(candidate_project):
                project = candidate_project
                reused_existing_workspace = True
                break

        if project is None:
            project = await pm.create_project_workspace(
                app_session_id,
                project_name=arguments.get("project_name"),
                aep_filename=arguments.get("aep_filename"),
                set_active=False,
            )

        await pm.set_active_project(app_session_id, project["_id"])
        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(arguments.get("timeout_seconds")),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
        if not refreshed_project or not refreshed_project.get("aep_files"):
            fallback_result = None
            if not _coerce_bool(arguments.get("disable_safe_fallback"), False):
                fallback_result = await nr.run_jsx_script(
                    session_doc["container_id"],
                    _build_safe_after_effects_demo_script(),
                    project=project,
                    timeout_seconds=_coerce_timeout_seconds(arguments.get("timeout_seconds")),
                )
                refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
                if refreshed_project and refreshed_project.get("aep_files"):
                    result = {
                        **fallback_result,
                        "safe_fallback_used": True,
                        "initial_script_result": result,
                    }

        if not refreshed_project or not refreshed_project.get("aep_files"):
            payload = {
                **result,
                "project_id": project["_id"],
                "workspace_dir": project["workspace_dir"],
                "entry_aep_file": project.get("entry_aep_file"),
                "entry_aep_path": str(Path(project["workspace_dir"]) / project["entry_aep_file"]),
                "reused_existing_workspace": reused_existing_workspace,
            }
            if fallback_result is not None:
                payload["safe_fallback_attempt"] = fallback_result
            return ToolResult(
                text_result_for_llm=json.dumps(payload, ensure_ascii=False),
                result_type="failure",
                error=(
                    "JSX did not save an .aep file into the managed project workspace. "
                    "Reuse this same project_id on the next retry instead of creating another workspace, "
                    "and leave the intended project open or save it to SHOTWRIGHT_PROJECT_FILE so the wrapper can persist it."
                ),
                session_log=arguments.get("description") or default_description,
            )

        await pm.set_active_project(app_session_id, refreshed_project["_id"])
        active_project = await pm.get_project(app_session_id, refreshed_project["_id"]) or refreshed_project
        entry_aep_file = active_project.get("entry_aep_file") or (active_project.get("aep_files") or [None])[0]
        payload = {
            **result,
            "project_id": active_project["_id"],
            "filename": active_project["filename"],
            "origin": active_project.get("origin", "generated"),
            "workspace_dir": active_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(active_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": active_project.get("aep_files", []),
            "compositions": active_project.get("compositions", []),
            "composition_catalog_updated_at": active_project.get("composition_catalog_updated_at"),
            "reused_existing_workspace": reused_existing_workspace,
        }
        return _tool_success(
            payload,
            arguments.get("description") or default_description,
        )

    async def inspect_workspace(invocation: ToolInvocation) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container = None
        if session_doc.get("container_id"):
            container = await cm.get_container(session_doc["container_id"])

        projects = await pm.list_projects(app_session_id)
        recent_image_attachments = await _list_session_image_attachments(app_session_id, limit=6)
        payload = {
            "session_id": app_session_id,
            "status": session_doc.get("status"),
            "container": {
                "id": container.get("_id"),
                "status": container.get("status"),
                "docker_id": container.get("docker_id"),
            }
            if container
            else None,
            "active_project_id": session_doc.get("active_project_id"),
            "projects": [
                {
                    "project_id": project["_id"],
                    "filename": project["filename"],
                    "origin": project.get("origin", "uploaded"),
                    "entry_aep_file": project.get("entry_aep_file"),
                    "aep_files": project.get("aep_files", []),
                    "compositions": project.get("compositions", []),
                    "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
                    "workspace_dir": project["workspace_dir"],
                }
                for project in projects
            ],
            "recent_image_attachments": recent_image_attachments,
            "latest_render_path": session_doc.get("latest_render_path"),
            "latest_stream_url": session_doc.get("latest_stream_url"),
            "render_outputs": nr.list_render_outputs(app_session_id, limit=8),
            "reference_videos": rm.list_reference_videos(app_session_id, limit=8),
            "storyboards": rm.list_storyboards(app_session_id, limit=8),
        }
        return _tool_success(payload, "Loaded Shotwright workspace state")

    async def ensure_after_effects_container(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container_id = session_doc.get("container_id")
        if container_id:
            container = await cm.get_container(container_id)
            if container and container.get("status") == "running":
                return _tool_success(
                    {
                        "container_id": container["_id"],
                        "docker_id": container["docker_id"],
                        "status": container["status"],
                    },
                    "Reused existing running After Effects container",
                )

        created = await cm.create_container(app_session_id, args.get("image"))
        return _tool_success(
            {
                "container_id": created["_id"],
                "docker_id": created["docker_id"],
                "status": created["status"],
                "image": created["image"],
            },
            "Started a new Shotwright After Effects container",
        )

    async def create_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        script_content = (args.get("script_content") or "").strip()
        if not script_content:
            return _tool_failure("script_content is required.")

        return await _create_project_from_script(
            arguments=args,
            script_content=script_content,
            default_description="Created managed After Effects project",
        )

    async def create_empty_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project = None
        reused_existing_workspace = False
        candidate_project_ids: list[str] = []

        requested_project_id = str(args.get("project_id") or "").strip()
        if requested_project_id:
            candidate_project_ids.append(requested_project_id)

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if active_project_id and active_project_id not in candidate_project_ids:
            candidate_project_ids.append(active_project_id)

        for candidate_project_id in candidate_project_ids:
            candidate_project = await pm.get_project(app_session_id, candidate_project_id)
            if _should_reuse_generated_project_workspace(candidate_project):
                project = candidate_project
                reused_existing_workspace = True
                break

        if project is None:
            project = await pm.create_project_workspace(
                app_session_id,
                project_name=args.get("project_name"),
                aep_filename=args.get("aep_filename"),
                set_active=False,
            )

        bootstrap_template_path = nr._resolve_nexrender_bootstrap_template()
        if not bootstrap_template_path.exists():
            return _tool_failure(f"Bootstrap template not found at {bootstrap_template_path}")

        entry_aep_file = project.get("entry_aep_file") or project.get("filename")
        if not entry_aep_file:
            return _tool_failure("Managed project is missing entry_aep_file.")

        target_aep_path = Path(project["workspace_dir"]) / entry_aep_file
        if not target_aep_path.exists():
            target_aep_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bootstrap_template_path, target_aep_path)

        metadata_path = Path(project["workspace_dir"]) / pm.PROJECT_METADATA_FILENAME
        metadata_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "compositions": [{"name": nr.BOOTSTRAP_TEMPLATE_COMPOSITION}],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        await pm.set_active_project(app_session_id, project["_id"])
        refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"]) or project
        refreshed_entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]

        payload = {
            "success": True,
            "bootstrap_template_path": str(bootstrap_template_path),
            "project_id": refreshed_project["_id"],
            "filename": refreshed_project["filename"],
            "origin": refreshed_project.get("origin", "generated"),
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": refreshed_entry_aep_file,
            "entry_aep_path": (
                str(Path(refreshed_project["workspace_dir"]) / refreshed_entry_aep_file)
                if refreshed_entry_aep_file
                else None
            ),
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "reused_existing_workspace": reused_existing_workspace,
        }
        return _tool_success(
            payload,
            args.get("description") or "Created empty After Effects project",
        )

    async def list_uploaded_projects(invocation: ToolInvocation) -> ToolResult:
        projects = await pm.list_projects(app_session_id)
        payload = {
            "projects": [
                {
                    "project_id": project["_id"],
                    "filename": project["filename"],
                    "origin": project.get("origin", "uploaded"),
                    "entry_aep_file": project.get("entry_aep_file"),
                    "aep_files": project.get("aep_files", []),
                    "compositions": project.get("compositions", []),
                    "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
                    "workspace_dir": project["workspace_dir"],
                }
                for project in projects
            ]
        }
        return _tool_success(payload, "Listed Shotwright session projects")

    async def select_active_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        project_id = args.get("project_id")
        if not project_id:
            return _tool_failure("project_id is required.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        await pm.set_active_project(app_session_id, project_id)
        return _tool_success(
            {
                "project_id": project_id,
                "filename": project["filename"],
                "origin": project.get("origin", "uploaded"),
                "entry_aep_file": project.get("entry_aep_file"),
                "aep_files": project.get("aep_files", []),
                "compositions": project.get("compositions", []),
                "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
            },
            f"Selected project {project['filename']} as active",
        )

    async def stage_reference_images(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Create or select a project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        staged_images = await _stage_session_image_attachments(
            app_session_id,
            project,
            latest_only=_coerce_bool(args.get("latest_only"), default=True),
            asset_name=args.get("asset_name"),
        )
        if not staged_images:
            return _tool_failure(
                "No session image attachments are available. Send an inline image first or provide a project-relative reference asset path."
            )

        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
        payload = {
            "project_id": project_id,
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "staged_images": staged_images,
            "default_reference_asset_path": staged_images[0]["project_asset_path"],
            "default_reference_relative_path": staged_images[0]["project_relative_path"],
        }
        return _tool_success(payload, args.get("description") or "Staged reference images into the active project")

    async def create_reference_composition(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Create or select a project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        raw_reference_path = str(args.get("reference_asset_path") or "").strip()
        if raw_reference_path:
            source_path = Path(raw_reference_path)
            if not source_path.is_absolute():
                source_path = Path(project["workspace_dir"]) / raw_reference_path
            try:
                reference_asset = _copy_asset_into_project(
                    project,
                    source_path,
                    display_name=source_path.name,
                    asset_name=args.get("asset_name"),
                )
            except FileNotFoundError as exc:
                return _tool_failure(str(exc))
        else:
            staged_images = await _stage_session_image_attachments(
                app_session_id,
                project,
                latest_only=True,
                asset_name=args.get("asset_name"),
            )
            if not staged_images:
                return _tool_failure(
                    "No session image attachments are available. Send an inline image first or call stage_reference_images."
                )
            reference_asset = staged_images[0]

        composition_name = str(args.get("composition_name") or "Main").strip() or "Main"
        width = max(16, int(args.get("width") or 1920))
        height = max(16, int(args.get("height") or 1080))
        duration_seconds = max(1.0, float(args.get("duration_seconds") or 10.0))
        frame_rate = max(1.0, float(args.get("frame_rate") or 30.0))
        fit_mode = str(args.get("fit_mode") or "cover").strip().lower() or "cover"
        reset_existing = _coerce_bool(args.get("reset_existing"), default=False)

        result = await nr.run_jsx_script(
            session_doc["container_id"],
            _build_reference_composition_jsx(
                reference_asset_path=reference_asset["project_asset_path"],
                composition_name=composition_name,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                frame_rate=frame_rate,
                fit_mode=fit_mode,
                reset_existing=reset_existing,
            ),
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
        )

        await pm.set_active_project(app_session_id, project_id)
        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
        payload = {
            **result,
            "project_id": project_id,
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "reference_asset_path": reference_asset["project_asset_path"],
            "reference_relative_path": reference_asset["project_relative_path"],
            "composition_name": composition_name,
            "duration_seconds": duration_seconds,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
        }

        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=json.dumps(payload, ensure_ascii=False),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or f"Created or updated composition {composition_name}",
        )

    async def generate_storyboard_from_reference_video(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        try:
            payload = rm.generate_storyboard(
                app_session_id,
                reference_video_path=args.get("reference_video_path"),
                output_name=args.get("output_name"),
                start_seconds=float(args.get("start_seconds")) if args.get("start_seconds") is not None else None,
                clip_duration_seconds=(
                    float(args.get("clip_duration_seconds")) if args.get("clip_duration_seconds") is not None else None
                ),
                interval_seconds=float(args.get("interval_seconds")) if args.get("interval_seconds") is not None else None,
                columns=int(args.get("columns")) if args.get("columns") is not None else None,
                width=int(args.get("width")) if args.get("width") is not None else None,
                crop=args.get("crop"),
            )
        except rm.ReferenceMediaUnavailableError as exc:
            return _tool_failure(str(exc), error=str(exc))
        except (FileNotFoundError, TypeError, ValueError) as exc:
            return _tool_failure(str(exc))

        return _tool_success(
            payload,
            args.get("description") or "Generated storyboard from the reference video",
        )

    async def run_after_effects_jsx(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        script_content = (args.get("script_content") or "").strip()
        if not script_content:
            return _tool_failure("script_content is required.")

        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc or not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        project = None
        if project_id:
            project = await pm.get_project(app_session_id, project_id)
            if not project:
                return _tool_failure(f"Project {project_id} not found.")

        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
        )
        payload = dict(result)
        if project:
            refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
            if refreshed_project:
                entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
                payload["project_id"] = refreshed_project["_id"]
                payload["workspace_dir"] = refreshed_project["workspace_dir"]
                payload["entry_aep_file"] = entry_aep_file
                payload["entry_aep_path"] = (
                    str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None
                )
                payload["aep_files"] = refreshed_project.get("aep_files", [])
                payload["compositions"] = refreshed_project.get("compositions", [])
                payload["composition_catalog_updated_at"] = refreshed_project.get("composition_catalog_updated_at")

        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=json.dumps(payload, ensure_ascii=False),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or "Executed After Effects JSX script",
        )

    async def render_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Use list_uploaded_projects and select_active_project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        await pm.set_active_project(app_session_id, project_id)

        try:
            render = await nr.render_project(
                session_id=app_session_id,
                project_id=project_id,
                container_db_id=session_doc["container_id"],
                aep_relative_path=args.get("aep_file"),
                composition=args.get("composition") or "Main",
                output_name=args.get("output_name"),
                patch_script=args.get("patch_script"),
            )
        except (FileNotFoundError, ValueError) as exc:
            return _tool_failure(str(exc).strip() or exc.__class__.__name__)

        if not render["success"]:
            failure_payload = {
                **render,
                "project_id": project_id,
                "requested_composition": args.get("composition") or "Main",
                "failure_details": nr.format_render_failure_details(
                    render,
                    composition=args.get("composition") or "Main",
                ),
            }
            return ToolResult(
                text_result_for_llm=_serialize_tool_payload(failure_payload),
                result_type="failure",
                error=failure_payload["failure_details"],
                session_log="After Effects render failed",
            )

        stream_result = await generate_hls(render["output_path"], render["stream_id"])
        latest_stream_url = stream_result.get("playlist_url") if stream_result.get("success") else None
        render_output = nr.record_render_output(
            session_id=app_session_id,
            project_id=project_id,
            output_path=render["output_path"],
            composition=args.get("composition") or "Main",
            aep_path=render["aep_path"],
            work_dir=render.get("work_dir"),
            stdout_path=render.get("stdout_path"),
            stderr_path=render.get("stderr_path"),
            stream_id=render.get("stream_id"),
            playlist_url=latest_stream_url,
            project_workspace_dir=project.get("workspace_dir"),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        await session_col.update_one(
            {"_id": app_session_id},
            {
                "$set": {
                    "active_project_id": project_id,
                    "latest_render_path": render["output_path"],
                    "latest_stream_id": render["stream_id"],
                    "latest_stream_url": latest_stream_url,
                }
            },
        )
        await publish_session_updated(app_session_id)
        await publish_context_refresh(
            app_session_id,
            "render.completed",
            project_id=project_id,
            composition=args.get("composition") or "Main",
            render_path=render["output_path"],
            render_id=render_output["id"],
        )

        payload = {
            **render,
            "playlist_url": latest_stream_url,
            "stream_ready": bool(latest_stream_url),
            "render_output": render_output,
            "project_id": project_id,
            "active_project_id": project_id,
            "project": {
                "_id": refreshed_project["_id"],
                "filename": refreshed_project["filename"],
                "workspace_dir": refreshed_project["workspace_dir"],
                "entry_aep_file": refreshed_project.get("entry_aep_file"),
                "aep_files": refreshed_project.get("aep_files", []),
                "compositions": refreshed_project.get("compositions", []),
                "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            },
        }
        return _tool_success(payload, f"Rendered project {project_id}")

    async def export_project_archive(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected.")

        archive = await pm.export_project(app_session_id, project_id)
        if not archive:
            return _tool_failure(f"Project {project_id} could not be exported.")

        return _tool_success(
            {
                "project_id": project_id,
                "archive_path": str(archive),
                "download_url": f"/api/projects/{app_session_id}/{project_id}/archive",
            },
            f"Exported project {project_id} as zip archive",
        )

    async def stop_after_effects_container(invocation: ToolInvocation) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc or not session_doc.get("container_id"):
            return _tool_failure("No container is attached to this session.")

        stopped = await cm.stop_container(session_doc["container_id"])
        if not stopped:
            return _tool_failure("Container could not be stopped.")

        return _tool_success(
            {
                "container_id": stopped["_id"],
                "status": stopped["status"],
            },
            "Stopped After Effects container",
        )

    return [
        Tool(
            name="inspect_workspace",
            description="Read the current Shotwright session state, recent image attachments, uploaded reference videos, generated storyboards, container status, uploaded projects, and latest render info.",
            handler=inspect_workspace,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
        Tool(
            name="ensure_after_effects_container",
            description="Start an After Effects container for the current session if one is not already running.",
            handler=ensure_after_effects_container,
            parameters={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Optional Shotwright image override",
                    }
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="create_after_effects_project",
            description=(
                "Create a managed Shotwright project workspace, or reuse the current empty generated workspace after a failed bootstrap, "
                "then run an After Effects JSX script to save an .aep into it and keep that project active for later render/export steps."
            ),
            handler=create_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional generated project identifier to reuse after an earlier bootstrap failed before saving an .aep",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Human-readable project name used for the default .aep file name",
                    },
                    "aep_filename": {
                        "type": "string",
                        "description": "Optional .aep filename to save inside the managed workspace",
                    },
                    "script_content": {
                        "type": "string",
                        "description": (
                            "Complete JSX script source. Use the current open project, avoid app.newProject() in the warmed host, and save to SHOTWRIGHT_PROJECT_FILE only when you need an explicit path."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the creation step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
                "required": ["script_content"],
            },
            skip_permission=True,
        ),
        Tool(
            name="create_empty_after_effects_project",
            description="Create a blank managed Shotwright .aep, or reuse the current empty generated workspace after a failed bootstrap, without requiring handwritten JSX boilerplate.",
            handler=create_empty_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional generated project identifier to reuse after an earlier bootstrap failed before saving an .aep",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Human-readable project name used for the default .aep file name",
                    },
                    "aep_filename": {
                        "type": "string",
                        "description": "Optional .aep filename to save inside the managed workspace",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the creation step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="list_uploaded_projects",
            description="List all managed or uploaded Shotwright session projects, including discovered .aep files.",
            handler=list_uploaded_projects,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
        Tool(
            name="select_active_project",
            description="Mark one uploaded project as the active project for subsequent After Effects actions.",
            handler=select_active_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project identifier returned by list_uploaded_projects",
                    }
                },
                "required": ["project_id"],
            },
            skip_permission=True,
        ),
        Tool(
            name="stage_reference_images",
            description="Copy recent inline image attachments from the session transcript into the active project workspace and return stable project asset paths.",
            handler=stage_reference_images,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "latest_only": {
                        "type": "boolean",
                        "description": "When true, stage only the most recent session image attachment",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Optional stable file name to use inside the project workspace",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the asset staging step",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="generate_storyboard_from_reference_video",
            description="Generate a storyboard contact sheet from a session-local video clip, including uploaded reference videos and rendered mp4 exports, using ffmpeg sampling parameters without shell fallback.",
            handler=generate_storyboard_from_reference_video,
            parameters={
                "type": "object",
                "properties": {
                    "reference_video_path": {
                        "type": "string",
                        "description": "Optional shared-relative or absolute path to a session-local video clip, including uploaded reference videos and latest_render_path-style export paths; defaults to the newest uploaded reference video",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional jpg file name for the generated storyboard image",
                    },
                    "start_seconds": {
                        "type": "number",
                        "description": "Optional ffmpeg -ss style clip start in seconds",
                    },
                    "clip_duration_seconds": {
                        "type": "number",
                        "description": "Optional ffmpeg -t style clip duration in seconds",
                    },
                    "interval_seconds": {
                        "type": "number",
                        "description": "Frame sampling interval in seconds; lower values create denser storyboards",
                    },
                    "columns": {
                        "type": "integer",
                        "description": "Storyboard grid column count",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Per-frame tile width in pixels before tiling",
                    },
                    "crop": {
                        "type": "string",
                        "description": "Optional crop box to inspect local motion, formatted as x,y,width,height or x:y:width:height in pixels or percentages like 25%,10%,40%,35%.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the storyboard generation step",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="create_reference_composition",
            description="Create or update a composition in the active project using a staged reference image, without needing handwritten JSX for the common setup path.",
            handler=create_reference_composition,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "reference_asset_path": {
                        "type": "string",
                        "description": "Optional absolute or project-relative image path. When omitted, the most recent session image is staged automatically.",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Optional stable file name to use when copying the reference image into the project workspace",
                    },
                    "composition_name": {
                        "type": "string",
                        "description": "Composition name to create or update",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Composition width in pixels",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Composition height in pixels",
                    },
                    "duration_seconds": {
                        "type": "number",
                        "description": "Composition duration in seconds",
                    },
                    "frame_rate": {
                        "type": "number",
                        "description": "Composition frame rate",
                    },
                    "fit_mode": {
                        "type": "string",
                        "description": "Image fit mode: cover or contain",
                    },
                    "reset_existing": {
                        "type": "boolean",
                        "description": "When true, clear existing layers in the target comp before inserting the reference image",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the composition setup step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="run_after_effects_jsx",
            description=(
                "Execute a JSX script inside the active After Effects container. When a project_id or active project exists, "
                "the script can use SHOTWRIGHT_PROJECT_ROOT and SHOTWRIGHT_PROJECT_FILE to save updates back into the managed workspace."
            ),
            handler=run_after_effects_jsx,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "script_content": {
                        "type": "string",
                        "description": "Complete JSX script source",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the operation",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
                "required": ["script_content"],
            },
            skip_permission=True,
        ),
        Tool(
            name="render_after_effects_project",
            description=(
                "Render a managed or uploaded After Effects project through nexrender-cli and prepare an HLS preview. "
                "When patch_script is provided, Shotwright first persists that JSX into the managed project so later exports match the preview."
            ),
            handler=render_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "aep_file": {
                        "type": "string",
                        "description": "Relative path to the .aep file inside the uploaded archive",
                    },
                    "composition": {
                        "type": "string",
                        "description": "Composition name to render",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional output mp4 file name",
                    },
                    "patch_script": {
                        "type": "string",
                        "description": "Optional absolute path to a JSX patch asset; Shotwright will persist it into the managed project before rendering",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="export_project_archive",
            description="Create a downloadable zip archive for the active uploaded project using the current managed workspace state.",
            handler=export_project_archive,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    }
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="stop_after_effects_container",
            description="Stop the session's running After Effects container when work is complete.",
            handler=stop_after_effects_container,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
    ]
