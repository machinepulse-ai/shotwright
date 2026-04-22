(function () {
    app.beginUndoGroup("Create Round1 Animation");

    var project = app.project;
    if (!project) {
        throw new Error("No active After Effects project.");
    }

    function removeCompNamed(name) {
        for (var i = project.numItems; i >= 1; i--) {
            var item = project.item(i);
            if (item instanceof CompItem && item.name === name) {
                item.remove();
            }
        }
    }

    removeCompNamed("Main");
    removeCompNamed("main");

    var comp = project.items.addComp("Main", 1280, 720, 1.0, 2.0, 30);
    comp.bgColor = [0.03, 0.08, 0.22];
    comp.workAreaStart = 0;
    comp.workAreaDuration = 2.0;

    var bg = comp.layers.addSolid([0.03, 0.08, 0.22], "Background", 1280, 720, 1.0, 2.0);
    bg.moveToEnd();
    bg.locked = true;

    var circle = comp.layers.addShape();
    circle.name = "White Circle";

    var contents = circle.property("ADBE Root Vectors Group");
    var group = contents.addProperty("ADBE Vector Group");
    group.name = "Circle Group";

    var groupContents = group.property("ADBE Vectors Group");
    var ellipse = groupContents.addProperty("ADBE Vector Shape - Ellipse");
    ellipse.property("ADBE Vector Ellipse Size").setValue([120, 120]);

    var fill = groupContents.addProperty("ADBE Vector Graphic - Fill");
    fill.property("ADBE Vector Fill Color").setValue([1, 1, 1]);

    var position = circle.property("ADBE Transform Group").property("ADBE Position");
    position.setValueAtTime(0, [60, 360]);
    position.setValueAtTime(2, [1220, 360]);
    position.setInterpolationTypeAtKey(1, KeyframeInterpolationType.LINEAR, KeyframeInterpolationType.LINEAR);
    position.setInterpolationTypeAtKey(2, KeyframeInterpolationType.LINEAR, KeyframeInterpolationType.LINEAR);

    if (project.file) {
        project.save(project.file);
    }

    app.endUndoGroup();
})();
