'use strict';

function get(collection, idx) {
    return collection[idx];
}

function map(collection, callback) {
    var mapped = [];
    for (var i = 1; i <= collection.length; i++) {
        var item = get(collection, i);
        mapped.push(callback(item, i));
    }
    return mapped;
}

function toArray(collection) {
    return map(collection, function (item) {
        return item;
    });
}

function findCompByName(items, compName) {
    for (var index = 0; index < items.length; index++) {
        var item = items[index];
        if (item instanceof CompItem && item.name === compName) {
            return item;
        }
    }
    return undefined;
}

function replaceWords() {
    var project = app.project;
    var compName = NX.get('comp') || 'main';
    var stretch = NX.get('stretch') || 100;
    var items = toArray(project.items);
    var comp = findCompByName(items, compName);

    if (comp === undefined) {
        throw new Error('validation_patch: composition not found: ' + compName);
    }

    var layers = toArray(comp.layers);
    var textLayers = ['text', 'text_main', 'text_sub'];
    for (var index = 0; index < layers.length; index++) {
        var layer = layers[index];
        layer.stretch = stretch;
        if (textLayers.indexOf(layer.name) === -1) {
            continue;
        }
        var text = NX.get(layer.name) || '';
        var textProperty = layer.property('Source Text');
        textProperty.setValue(new TextDocument(text));
    }
}

replaceWords();
