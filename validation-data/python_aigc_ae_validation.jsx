(function () {
    app.beginUndoGroup("Shotwright Python AIGC validation comp");

    var projectRoot = $.getenv("SHOTWRIGHT_PROJECT_ROOT");
    if (!projectRoot) {
        throw new Error("SHOTWRIGHT_PROJECT_ROOT is not set");
    }
    projectRoot = projectRoot.replace(/\\/g, "/");
    var assetRoot = projectRoot + "/assets/python";

    function filePath(name) {
        return assetRoot + "/" + name;
    }

    function prop(group, name) {
        try { return group ? group.property(name) : null; } catch (error) { return null; }
    }

    function setValue(target, value) {
        try { if (target) { target.setValue(value); } } catch (error) {}
    }

    function keyValue(target, time, value) {
        try { if (target) { target.setValueAtTime(time, value); } } catch (error) {}
    }

    function removeAllItems() {
        while (app.project.items.length > 0) {
            try { app.project.items[1].remove(); } catch (error) { break; }
        }
    }

    function importFootage(name, label) {
        var file = new File(filePath(name));
        if (!file.exists) {
            throw new Error("Missing Python asset: " + file.fsName);
        }
        var item = app.project.importFile(new ImportOptions(file));
        item.name = label || name;
        return item;
    }

    function fitLayer(layer, comp, maxWidth, maxHeight) {
        if (!layer || !layer.source) { return; }
        var scale = Math.min((maxWidth / layer.source.width) * 100, (maxHeight / layer.source.height) * 100);
        setValue(prop(prop(layer, "ADBE Transform Group"), "ADBE Scale"), [scale, scale]);
    }

    function styleText(layer, size, color, justification) {
        var docProp = prop(prop(layer, "ADBE Text Properties"), "ADBE Text Document");
        var doc = docProp.value;
        doc.fontSize = size;
        doc.fillColor = color;
        doc.justification = justification || ParagraphJustification.LEFT_JUSTIFY;
        docProp.setValue(doc);
    }

    removeAllItems();

    var width = 1280;
    var height = 720;
    var duration = 6;
    var fps = 30;
    var comp = app.project.items.addComp("Main", width, height, 1, duration, fps);
    comp.bgColor = [0.01, 0.014, 0.025];
    comp.motionBlur = true;
    comp.shutterAngle = 180;

    var dashboard = importFootage("python_aigc_dashboard.png", "Python AIGC dashboard");
    var motionVideo = importFootage("opencv_motion.mp4", "OpenCV motion analysis video");
    var audio = importFootage("synthetic_voice_bed.wav", "Synthetic audio bed");

    var bg = comp.layers.addSolid([0.01, 0.014, 0.025], "deep technical base", width, height, 1, duration);
    bg.moveToEnd();

    var dashLayer = comp.layers.add(dashboard);
    dashLayer.name = "Python-generated dashboard plate";
    fitLayer(dashLayer, comp, 1110, 625);
    setValue(prop(prop(dashLayer, "ADBE Transform Group"), "ADBE Position"), [640, 385]);
    keyValue(prop(prop(dashLayer, "ADBE Transform Group"), "ADBE Opacity"), 0, 0);
    keyValue(prop(prop(dashLayer, "ADBE Transform Group"), "ADBE Opacity"), 0.45, 100);
    keyValue(prop(prop(dashLayer, "ADBE Transform Group"), "ADBE Scale"), 0, [84, 84]);
    keyValue(prop(prop(dashLayer, "ADBE Transform Group"), "ADBE Scale"), duration, [89, 89]);

    var videoLayer = comp.layers.add(motionVideo);
    videoLayer.name = "OpenCV generated motion insert";
    fitLayer(videoLayer, comp, 360, 202);
    setValue(prop(prop(videoLayer, "ADBE Transform Group"), "ADBE Position"), [985, 542]);
    keyValue(prop(prop(videoLayer, "ADBE Transform Group"), "ADBE Opacity"), 0.8, 0);
    keyValue(prop(prop(videoLayer, "ADBE Transform Group"), "ADBE Opacity"), 1.35, 100);

    var audioLayer = comp.layers.add(audio);
    audioLayer.name = "Librosa synthetic audio bed";
    setValue(prop(prop(audioLayer, "ADBE Transform Group"), "ADBE Opacity"), 0);

    var title = comp.layers.addText("PYTHON + AFTER EFFECTS");
    title.name = "validation title";
    styleText(title, 46, [0.78, 0.96, 1], ParagraphJustification.CENTER_JUSTIFY);
    setValue(prop(prop(title, "ADBE Transform Group"), "ADBE Position"), [640, 74]);
    keyValue(prop(prop(title, "ADBE Transform Group"), "ADBE Opacity"), 0, 0);
    keyValue(prop(prop(title, "ADBE Transform Group"), "ADBE Opacity"), 0.35, 100);

    var subtitle = comp.layers.addText("7 CPU-only media cases generated assets, analyzed motion/audio, and fed this comp");
    subtitle.name = "validation subtitle";
    styleText(subtitle, 22, [0.58, 0.9, 0.74], ParagraphJustification.CENTER_JUSTIFY);
    setValue(prop(prop(subtitle, "ADBE Transform Group"), "ADBE Position"), [640, 124]);
    keyValue(prop(prop(subtitle, "ADBE Transform Group"), "ADBE Opacity"), 0.35, 0);
    keyValue(prop(prop(subtitle, "ADBE Transform Group"), "ADBE Opacity"), 0.9, 100);

    for (var i = 0; i < 24; i += 1) {
        var barHeight = 28 + ((i * 37) % 130);
        var bar = comp.layers.addSolid([0.1 + (i % 3) * 0.18, 0.72, 0.95 - (i % 4) * 0.1], "audio feature bar " + i, 18, barHeight, 1, duration);
        var x = 126 + i * 32;
        var y = 642 - barHeight / 2;
        setValue(prop(prop(bar, "ADBE Transform Group"), "ADBE Position"), [x, y]);
        keyValue(prop(prop(bar, "ADBE Transform Group"), "ADBE Scale"), 0.55 + i * 0.025, [100, 15]);
        keyValue(prop(prop(bar, "ADBE Transform Group"), "ADBE Scale"), 1.15 + i * 0.025, [100, 100]);
        keyValue(prop(prop(bar, "ADBE Transform Group"), "ADBE Opacity"), 0.35, 0);
        keyValue(prop(prop(bar, "ADBE Transform Group"), "ADBE Opacity"), 1.1, 78);
        bar.motionBlur = true;
    }

    try {
        var glow = dashLayer.property("ADBE Effect Parade").addProperty("ADBE Glow");
        setValue(prop(glow, "ADBE Glow-0002"), 34);
        setValue(prop(glow, "ADBE Glow-0003"), 0.42);
    } catch (glowError) {}

    comp.openInViewer();
    app.endUndoGroup();
}());
