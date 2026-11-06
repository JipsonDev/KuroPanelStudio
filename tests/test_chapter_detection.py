"""Chapter YOLO runs serially, preserves existing work and commits atomically."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import pytest
from core.project_manager import Page


def test_chapter_detection_keeps_one_page_in_flight_and_maps_progress():
    from core.chapter_detection import detect_chapter
    calls, progress = [], []
    class Detector:
        def detect(self, path, update, cancelled, states):
            calls.append((path.name, states))
            update(50)
            return [{'x':1,'y':2,'width':30,'height':12}]
    pages=[Page(f'{i}.png',Path(f'{i}.png'),900,2000) for i in range(3)]
    result=detect_chapter(Detector(),pages,{'1.png':{'ink':{'visible':False}}},
                          lambda value,*args:progress.append(value),lambda:False)
    assert [name for name,_ in calls]==['0.png','1.png','2.png']
    assert calls[1][1]=={'ink':{'visible':False}}
    assert set(result)=={'0.png','1.png','2.png'}
    assert progress==sorted(progress) and progress[-1]==100


def test_cancelled_chapter_returns_no_partial_results():
    from core.chapter_detection import detect_chapter
    calls=[]
    class Detector:
        def detect(self,path,*args):
            calls.append(path)
            return [{'x':1,'y':2,'width':30,'height':12}]
    pages=[Page(f'{i}.png',Path(f'{i}.png'),900,2000) for i in range(3)]
    assert detect_chapter(Detector(),pages,{},lambda *args:None,lambda:bool(calls))=={}
    assert len(calls)==1


def test_yolo_scope_does_not_depend_on_ocr_credentials():
    from PySide6.QtWidgets import QApplication,QPushButton
    from ui.ai_panel import AIOptionsPanel
    app=QApplication.instance() or QApplication([])
    panel=AIOptionsPanel();panel.show();panel.set_ready('ocr',False)
    actions=[];panel.action_requested.connect(actions.append)
    panel.detect_scope.setCurrentIndex(1)
    panel.detect_button.click()
    panel.detect_scope.setCurrentIndex(0)
    panel.detect_button.click()
    assert actions==['run_yolo_all','run_yolo']
    assert len([b for b in panel.findChildren(QPushButton) if b.text().startswith('Detectar cajas')])==1
    panel.close()


def test_batch_detection_keeps_existing_boxes_and_applies_to_the_correct_pages(tmp_path,monkeypatch):
    from unittest.mock import patch
    from PIL import Image
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow
    from test_strip_view import app_wait
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'data'))
    pages=[]
    for i in range(3):
        path=tmp_path/f'{i}.png';Image.new('RGB',(320,500),'white').save(path)
        pages.append(Page(path.name,path,320,500))
    with patch.object(MainWindow,'_offer_recovery'),patch.object(MainWindow,'_import_legacy_profiles_async'),patch.object(MainWindow,'_warm_up_models'),patch.object(MainWindow,'_check_for_updates'):
        w=MainWindow();w.project.pages=pages;w.show();w._set_page(0)
        app_wait(app,lambda:w._displayed_page_path==pages[0].path)
        original={'id':'saved','x':10,'y':10,'width':50,'height':20,'text':'OCR saved','translation':'Traducción'}
        w._show_regions([original.copy()]);w.history.reset(w._snapshot())
        operations=[]
        monkeypatch.setattr(w,'_start_task',lambda operation,*args,**kwargs:operations.append((operation,args[2])))
        calls=[]
        def detect(path,progress,cancelled,states):
            calls.append(path);progress(100)
            return [{'x':20,'y':30,'width':70,'height':25,'confidence':.9}]
        monkeypatch.setattr(w.detector,'detect',detect)
        w.run_detection_chapter()
        operation,finished=operations.pop()
        result=operation(lambda *args:None,lambda:False)
        assert calls==[pages[1].path,pages[2].path]
        assert w.page_regions[str(pages[0].path)][0]['text']=='OCR saved'
        assert str(pages[1].path) not in w.page_regions
        finished(result)
        assert w.page_regions[str(pages[0].path)][0]['translation']=='Traducción'
        assert all(len(w.page_regions[str(p.path)])==1 for p in pages)
        assert all(w.page_regions[str(p.path)][0].get('id') for p in pages)
        assert w.project.active_index==0
        w.undo()
        assert not w.page_regions.get(str(pages[1].path))
        assert w.page_regions[str(pages[0].path)][0]['text']=='OCR saved'
        w.run_detection_chapter()
        operation,finished=operations.pop()
        result=operation(lambda *args:None,lambda:False)
        manual={'id':'new-manual','x':10,'y':10,'width':80,'height':20,'text':'Created during YOLO'}
        w.page_regions[str(pages[2].path)]=[manual]
        finished(result)
        assert w.page_regions[str(pages[2].path)]==[manual]
        w.close()


def test_failed_page_aborts_the_batch_instead_of_returning_partial_detections():
    from core.chapter_detection import detect_chapter
    class Detector:
        def detect(self,path,*args):
            if path.name=='1.png':raise RuntimeError('Unreadable image')
            return [{'x':1,'y':2,'width':30,'height':12}]
    pages=[Page(f'{i}.png',Path(f'{i}.png'),900,2000) for i in range(3)]
    with pytest.raises(RuntimeError,match='Unreadable image'):
        detect_chapter(Detector(),pages,{},lambda *args:None,lambda:False)
