"""Document actions live in the header; the inspector edits the selection."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QPushButton
from ui.topbar import TopBar
from ui.inspector_panel import InspectorPanel


def test_header_offers_all_export_scopes_and_editable_project_opening():
    app=QApplication.instance() or QApplication([])
    bar=TopBar();actions=[];formats=[]
    bar.action_requested.connect(actions.append)
    bar.export_format_changed.connect(formats.append)
    bar.export_actions['save_all'].trigger()
    bar.export_actions['save_strip'].trigger()
    bar.export_formats['WEBP'].trigger()
    assert actions==['save_all','save_strip'] and formats==['WEBP']
    assert any(a.text()=='Abrir proyecto editable…' for a in bar.open_button.menu().actions())
    bar.close()


def test_inspector_contains_no_document_navigation_or_export_duplicates():
    app=QApplication.instance() or QApplication([])
    inspector=InspectorPanel()
    names={b.text() for b in inspector.findChildren(QPushButton)}
    assert not names.intersection({'Anterior','Siguiente','+','−','Exportar página','Exportar capítulo','Guardar proyecto','Abrir proyecto'})
    assert 'Aplicar texto' in names
    inspector.close()


def test_header_strip_export_routes_to_the_streaming_exporter(tmp_path,monkeypatch):
    from unittest.mock import patch
    from ui.main_window import MainWindow
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'data'))
    with patch.object(MainWindow,'_offer_recovery'),patch.object(MainWindow,'_import_legacy_profiles_async'),patch.object(MainWindow,'_warm_up_models'),patch.object(MainWindow,'_check_for_updates'):
        window=MainWindow();calls=[]
        from PIL import Image
        from core.project_manager import Page
        path=tmp_path/'page.png';Image.new('RGB',(320,500),'white').save(path)
        window.project.pages=[Page(path.name,path,320,500)]
        window.show();window._toggle_continuous_strip(True)
        monkeypatch.setattr(window,'save_strip',lambda:calls.append('strip'))
        window.topbar.export_actions['save_strip'].trigger()
        assert calls==['strip']
        assert window.canvas_shell.strip_button.isChecked()
        window.close()
