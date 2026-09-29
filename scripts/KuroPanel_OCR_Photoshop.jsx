#target photoshop

/*
KuroPanel Studio OCR bridge for Adobe Photoshop.

Usage:
1. Run File > Scripts > Browse... and choose this JSX.
2. Use "Detectar + OCR" for the typer workflow or "OCR seleccion" for one crop.
*/

(function () {
    if (!app.documents.length) {
        alert("Abre un documento antes de ejecutar OCR.");
        return;
    }

    var PROJECT_ROOT = new File($.fileName).parent.parent.fsName.replace(/\\/g, "/");
    var PYTHON_EXE = PROJECT_ROOT + "/.venv-gpu/Scripts/python.exe";
    if (!(new File(PYTHON_EXE)).exists) {
        PYTHON_EXE = PROJECT_ROOT + "/.venv/Scripts/python.exe";
    }
    var BRIDGE = PROJECT_ROOT + "/scripts/photoshop_ocr_bridge.py";
    if (!(new File(PYTHON_EXE)).exists || !(new File(BRIDGE)).exists) {
        alert("No se encontró Python o el puente OCR. Ejecuta este JSX desde la carpeta scripts del proyecto.");
        return;
    }

    function px(value) {
        return Math.round(value.as("px"));
    }

    function documentBounds(doc) {
        return [0, 0, px(doc.width), px(doc.height)];
    }

    function selectionBounds(doc) {
        try {
            var bounds = doc.selection.bounds;
            return [px(bounds[0]), px(bounds[1]), px(bounds[2]), px(bounds[3])];
        } catch (error) {
            return null;
        }
    }

    function activeLayerBounds(doc) {
        try {
            var bounds = doc.activeLayer.bounds;
            var result = [px(bounds[0]), px(bounds[1]), px(bounds[2]), px(bounds[3])];
            if (result[2] > result[0] && result[3] > result[1]) {
                return result;
            }
        } catch (error) {
        }
        return documentBounds(doc);
    }

    function q(path) {
        return '"' + String(path).replace(/\//g, "\\") + '"';
    }

    function safeName(prefix) {
        return prefix + "_" + (new Date()).getTime() + "_" + Math.floor(Math.random() * 100000);
    }

    function writeText(file, text) {
        file.encoding = "UTF-8";
        file.open("w");
        file.write(text);
        file.close();
    }

    function readText(file) {
        file.encoding = "UTF-8";
        file.open("r");
        var value = file.read();
        file.close();
        return value;
    }

    function parseJson(text) {
        if (typeof JSON !== "undefined" && JSON.parse) {
            return JSON.parse(text);
        }
        return eval("(" + text + ")");
    }

    function exportCrop(doc, bounds, outputFile) {
        var duplicate = doc.duplicate("kuro_ocr_export", true);
        app.activeDocument = duplicate;
        duplicate.crop([
            UnitValue(bounds[0], "px"),
            UnitValue(bounds[1], "px"),
            UnitValue(bounds[2], "px"),
            UnitValue(bounds[3], "px")
        ]);
        var options = new PNGSaveOptions();
        duplicate.saveAs(outputFile, options, true, Extension.LOWERCASE);
        duplicate.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = doc;
    }

    function runBridge(imageFile, outputFile, workFolder, mode) {
        var batFile = new File(workFolder.fsName + "/run_ocr.bat");
        var command = [
            "@echo off",
            "setlocal",
            "set \"PYTHON_EXE=" + PYTHON_EXE.replace(/\//g, "\\") + "\"",
            "if exist \"%PYTHON_EXE%\" (",
            "  \"%PYTHON_EXE%\" " + q(BRIDGE) + " --image " + q(imageFile.fsName) + " --output " + q(outputFile.fsName) + " --mode " + mode + " --script cjk",
            ") else (",
            "  py -3 " + q(BRIDGE) + " --image " + q(imageFile.fsName) + " --output " + q(outputFile.fsName) + " --mode " + mode + " --script cjk",
            ")"
        ].join("\r\n");
        writeText(batFile, command);
        if (!batFile.execute()) {
            throw new Error("No se pudo iniciar el puente OCR.");
        }

        var waited = 0;
        while (!outputFile.exists && waited < 180000) {
            $.sleep(500);
            waited += 500;
        }
        if (!outputFile.exists) {
            throw new Error("OCR agotó el tiempo de espera.");
        }
        return parseJson(readText(outputFile));
    }

    function addTextLayer(doc, bounds, text, group) {
        app.activeDocument = doc;
        var layer = doc.artLayers.add();
        layer.kind = LayerKind.TEXT;
        layer.name = "OCR - " + String(text).replace(/\r\n|\r|\n/g, " ").substr(0, 28);

        var width = Math.max(16, bounds[2] - bounds[0]);
        var height = Math.max(16, bounds[3] - bounds[1]);
        var lines = Math.max(1, String(text).split(/\r\n|\r|\n/).length);
        var size = Math.max(10, Math.min(42, Math.floor(height / (lines + 1))));

        var item = layer.textItem;
        item.kind = TextType.PARAGRAPHTEXT;
        item.contents = text;
        item.position = [UnitValue(bounds[0], "px"), UnitValue(bounds[1] + size, "px")];
        item.width = UnitValue(width, "px");
        item.height = UnitValue(height, "px");
        item.size = UnitValue(size, "px");
        item.justification = Justification.CENTER;
        if (group) {
            layer.move(group, ElementPlacement.INSIDE);
        }
        return layer;
    }

    function chooseAction() {
        var dialog = new Window("dialog", "KuroPanel Typer OCR");
        dialog.orientation = "column";
        dialog.alignChildren = "fill";
        dialog.spacing = 10;
        dialog.margins = 14;

        var note = dialog.add("statictext", undefined, "Detecta textos chinos, ejecuta OCR y crea capas de texto.");
        note.alignment = "fill";

        var detectButton = dialog.add("button", undefined, "Detectar + OCR");
        var oneButton = dialog.add("button", undefined, "OCR seleccion");
        var detectOnlyButton = dialog.add("button", undefined, "Solo detectar cajas");
        var cancelButton = dialog.add("button", undefined, "Cancelar");

        var value = null;
        detectButton.onClick = function () { value = "detect-ocr"; dialog.close(); };
        oneButton.onClick = function () { value = "single"; dialog.close(); };
        detectOnlyButton.onClick = function () { value = "detect"; dialog.close(); };
        cancelButton.onClick = function () { value = null; dialog.close(); };
        dialog.show();
        return value;
    }

    function addDetectedLayers(doc, cropBounds, result, withText) {
        var group = doc.layerSets.add();
        group.name = withText ? "KuroPanel Typer OCR" : "KuroPanel Cajas Detectadas";
        var regions = result.regions || [];
        for (var index = 0; index < regions.length; index++) {
            var region = regions[index];
            var x = cropBounds[0] + Number(region.x || 0);
            var y = cropBounds[1] + Number(region.y || 0);
            var w = Number(region.width || 0);
            var h = Number(region.height || 0);
            var text = withText ? String(region.text || "") : ("Caja " + (index + 1));
            if (!text) {
                continue;
            }
            addTextLayer(doc, [x, y, x + w, y + h], text, group);
        }
        return regions.length;
    }

    var mode = chooseAction();
    if (!mode) {
        return;
    }

    var doc = app.activeDocument;
    var hadSelection = selectionBounds(doc) !== null;
    var cropBounds = selectionBounds(doc);
    if (!cropBounds && mode === "single") {
        cropBounds = activeLayerBounds(doc);
    }
    if (!cropBounds) {
        cropBounds = documentBounds(doc);
    }

    var folder = new Folder(Folder.temp.fsName + "/" + safeName("kuro_panel_ocr"));
    if (!folder.create()) {
        alert("No se pudo crear carpeta temporal para OCR.");
        return;
    }

    var imageFile = new File(folder.fsName + "/crop.png");
    var outputFile = new File(folder.fsName + "/ocr_result.json");

    try {
        exportCrop(doc, cropBounds, imageFile);
        var result = runBridge(imageFile, outputFile, folder, mode);
        if (!result.ok) {
            alert("Error OCR: " + result.error);
            return;
        }

        if (mode === "detect") {
            var totalBoxes = addDetectedLayers(doc, cropBounds, result, false);
            alert("Cajas detectadas: " + totalBoxes);
            return;
        }

        if (mode === "detect-ocr") {
            var totalTextLayers = addDetectedLayers(doc, cropBounds, result, true);
            if (!totalTextLayers && hadSelection) {
                var fallbackOutput = new File(folder.fsName + "/ocr_result_single.json");
                var fallback = runBridge(imageFile, fallbackOutput, folder, "single");
                if (fallback.ok && fallback.text) {
                    addTextLayer(doc, cropBounds, fallback.text, null);
                    return;
                }
            }
            if (!totalTextLayers) {
                alert("No se detectaron textos para convertir en capas.");
            }
            return;
        }

        if (!result.text) {
            alert("OCR terminado, pero no se detectó texto.");
            return;
        }
        addTextLayer(doc, cropBounds, result.text, null);
    } catch (error) {
        alert("Error OCR: " + error.message);
    }
}());
