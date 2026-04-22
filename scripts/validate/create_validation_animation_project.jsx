function ensureFolder(path) {
    var folder = new Folder(path);
    if (!folder.exists) {
        folder.create();
    }
    return folder;
}

function addTextLayer(comp, name, text, position, fontSize) {
    var layer = comp.layers.addText(text);
    layer.name = name;

    var textProperty = layer.property("Source Text");
    var textDocument = textProperty.value;
    textDocument.text = text;
    textDocument.fontSize = fontSize;
    textDocument.justification = ParagraphJustification.CENTER_JUSTIFY;
    textDocument.fillColor = [1, 1, 1];
    textProperty.setValue(textDocument);

    layer.property("Position").setValue(position);
    return layer;
}

function addAnimatedSolid(comp, name, color, width, height, startScale, endScale, startOpacity, endOpacity, startTime, endTime, startPosition, endPosition) {
    var layer = comp.layers.addSolid(color, name, width, height, 1);
    var scaleProperty = layer.property("Scale");
    var opacityProperty = layer.property("Opacity");
    var positionProperty = layer.property("Position");

    scaleProperty.setValueAtTime(startTime, startScale);
    scaleProperty.setValueAtTime(endTime, endScale);
    opacityProperty.setValueAtTime(startTime, startOpacity);
    opacityProperty.setValueAtTime(endTime, endOpacity);
    positionProperty.setValueAtTime(startTime, startPosition);
    positionProperty.setValueAtTime(endTime, endPosition);

    return layer;
}

function addFadeAndSlide(layer, startTime, endTime, startPosition, endPosition) {
    var opacityProperty = layer.property("Opacity");
    var positionProperty = layer.property("Position");

    opacityProperty.setValueAtTime(startTime, 0);
    opacityProperty.setValueAtTime(startTime + 0.4, 100);
    opacityProperty.setValueAtTime(endTime - 0.4, 100);
    opacityProperty.setValueAtTime(endTime, 0);

    positionProperty.setValueAtTime(startTime, startPosition);
    positionProperty.setValueAtTime(endTime, endPosition);
}

app.beginSuppressDialogs();
if (typeof CloseOptions !== "undefined" && app.project && typeof app.project.close === "function") {
    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
}

var templatesRoot = $.getenv("SHOTWRIGHT_TEMPLATES_ROOT") || "C:/data/templates";
var defaultYear = $.getenv("SHOTWRIGHT_VALIDATION_YEAR") || "2026";

ensureFolder(templatesRoot);

var project = app.project;
var comp = project.items.addComp("main", 1920, 1080, 1, 4, 25);

var background = comp.layers.addSolid([0.08, 0.09, 0.12], "bg_base", 1920, 1080, 1);
background.moveToEnd();

var accentLeft = addAnimatedSolid(
    comp,
    "accent_left",
    [0.18, 0.62, 0.96],
    720,
    720,
    [70, 70],
    [115, 115],
    35,
    75,
    0,
    4,
    [420, 360],
    [500, 470]
);
accentLeft.property("Rotation").setValueAtTime(0, -10);
accentLeft.property("Rotation").setValueAtTime(4, 12);

var accentRight = addAnimatedSolid(
    comp,
    "accent_right",
    [0.98, 0.42, 0.20],
    620,
    620,
    [85, 85],
    [125, 125],
    20,
    60,
    0,
    4,
    [1500, 760],
    [1380, 620]
);
accentRight.property("Rotation").setValueAtTime(0, 8);
accentRight.property("Rotation").setValueAtTime(4, -14);

var textMain = addTextLayer(comp, "text_main", "Validation", [960, 360], 138);
var textSub = addTextLayer(comp, "text_sub", "nexrender container test", [960, 560], 72);
var textYear = addTextLayer(comp, "text", defaultYear, [960, 760], 96);

addFadeAndSlide(textMain, 0, 4, [960, 430], [960, 340]);
addFadeAndSlide(textSub, 0.2, 4, [960, 620], [960, 540]);
addFadeAndSlide(textYear, 0.4, 4, [960, 820], [960, 730]);

project.save(new File(templatesRoot + "/validation_motion.aep"));
app.quit();
