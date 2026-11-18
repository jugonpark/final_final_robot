"""Qt dashboard. AI/capture and UDP run outside the GUI event loop."""
from collections import deque
import json
import math
from pathlib import Path
import time
import signal

from PySide6.QtCore import Qt,QTimer,QRectF,QPointF,Signal,QEvent,QTranslator,QLibraryInfo
from PySide6.QtGui import QImage,QPainter,QColor,QPen,QFontDatabase
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QFormLayout,QLabel,QPushButton,QGroupBox,QTabWidget,QScrollArea,QSplitter,QLineEdit,QCheckBox,
    QMessageBox,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QPlainTextEdit,QProgressBar,QDoubleSpinBox,QSpinBox)

from python_controller.control.hand_drive import DEFAULT_MAPPING, DIRECTIONS, HandDrive
from python_controller.dashboard_runtime import DashboardEngine,VisionStream,PreviewStream
from python_controller.monitoring import motor_feedback
from python_controller.visualization.korean import ko,add_choices,MEANINGS,movement_block
from python_controller.visualization.natural_control import NaturalControlPanel
from python_controller.visualization.follow_control import FollowControlPanel
from python_controller.diagnostics import NetworkProbe,diagnostic_report,save_report


def text(value,precision=3):
    if value is None: return '미수신·미확인'
    if isinstance(value,bool):return '예' if value else '아니오'
    if isinstance(value,float): return f'{value:.{precision}f}'
    if isinstance(value,(dict,list,tuple)): return json.dumps(value,ensure_ascii=False)
    return ko(str(value))


class Fields(QGroupBox):
    def __init__(self,title,keys):
        super().__init__(ko(title))
        layout=QFormLayout(self);layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.values={}
        for key in keys:
            name=QLabel(ko(key));name.setWordWrap(True)
            value=QLabel(ko('UNKNOWN'));value.setWordWrap(True);value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.values[key]=value;layout.addRow(name,value)

    def update_values(self,values):
        for name,value in values.items():
            if name in self.values:self.values[name].setText(ko(text(value)))


class CameraView(QWidget):
    selected=Signal(int)
    def __init__(self):
        super().__init__();self.setMinimumSize(320,240)
        self.image=None;self.people=();self.target=None;self.image_rect=QRectF()

    def set_frame(self,result,target):
        import cv2
        self.people=result['frame'].people;self.target=target
        image=result['image'].copy()
        h,w=image.shape[:2]
        cv2.line(image,(w//2,0),(w//2,h),(120,130,145),1)
        for person in self.people:
            box=tuple(map(int,person.bbox));color=(70,211,255) if person.track_id==target else (143,190,100)
            cv2.rectangle(image,box[:2],box[2:],color,2)
            cv2.putText(image,f'ID {person.track_id}  {person.confidence:.2f}',(box[0],max(20,box[1]-8)),cv2.FONT_HERSHEY_SIMPLEX,.55,color,1,cv2.LINE_AA)
            if person.track_id==target:cv2.circle(image,((box[0]+box[2])//2,(box[1]+box[3])//2),5,color,-1)
        connections=((0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),
                     (9,13),(13,14),(14,15),(15,16),(13,17),(0,17),(17,18),(18,19),(19,20))
        for points,owner in result['hands']:
            color=(70,211,255) if owner==target else (150,150,150)
            for a,b in connections:cv2.line(image,tuple(map(int,points[a])),tuple(map(int,points[b])),color,1)
            for x,y in points:cv2.circle(image,(int(x),int(y)),3,color,-1)
        self._show_image(image)

    def show_capture(self, image):
        self.people = ()
        self.target = None
        self._show_image(image)

    def clear_capture(self):
        self.people = ()
        self.target = None
        self.image = None
        self.update()

    def _show_image(self,image):
        import cv2
        h,w=image.shape[:2]
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        self.image=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#0b1119'))
        if self.image is None:
            painter.setPen(QColor('#9eafc4'));painter.drawText(self.rect(),Qt.AlignCenter,'카메라 프레임 대기 중');return
        size=self.image.size();size.scale(self.size(),Qt.KeepAspectRatio)
        self.image_rect=QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
        painter.drawImage(self.image_rect,self.image)

    def mousePressEvent(self,event):
        if self.image is None or not self.image_rect.contains(event.position()):return
        x=(event.position().x()-self.image_rect.x())*self.image.width()/self.image_rect.width()
        y=(event.position().y()-self.image_rect.y())*self.image.height()/self.image_rect.height()
        from python_controller.vision.person_tracker import TargetSelector
        chosen=TargetSelector.at_point(self.people,x,y)
        if chosen is not None:self.selected.emit(chosen)


class MotionArrow(QWidget):
    def __init__(self):
        super().__init__();self.setMinimumHeight(150);self.data=None
    def set_data(self,data):self.data=data;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        center=QPointF(self.width()/2,self.height()/2+8)
        p.setPen(QPen(QColor('#314256'),1));p.drawLine(QPointF(center.x(),25),QPointF(center.x(),self.height()-10))
        p.drawLine(QPointF(25,center.y()),QPointF(self.width()-25,center.y()))
        p.setPen(QColor('#aab8c8'));p.drawText(12,18,ko('Command axes (proposed signs): +vx forward'))
        if not self.data:return
        for name,color in [('requested','#8195b4'),('safe','#4cdaa3')]:
            command=self.data.get(name)
            if command is None:continue
            dx=command['vy_cm_s']/5*55;dy=-command['vx_cm_s']/5*55
            length=math.hypot(dx,dy)
            if length>65:dx*=65/length;dy*=65/length
            end=QPointF(center.x()+dx,center.y()+dy)
            p.setPen(QPen(QColor(color),3));p.drawLine(center,end)
            if length>1:
                angle=math.atan2(dy,dx)
                for off in (-.6,.6):p.drawLine(end,QPointF(end.x()-10*math.cos(angle+off),end.y()-10*math.sin(angle+off)))
        p.setPen(QColor('#4cdaa3'));p.drawText(12,self.height()-8,ko(f'Safe rotation: {self.data["safe"]["w_rad_s"]:+.3f} rad/s'))


class MotorGraph(QWidget):
    def __init__(self):
        super().__init__();self.setMinimumHeight(270);self.history=deque(maxlen=250);self.last_rx=None
    def observe(self,data):
        tr,m=data['transport'],data['motors']
        if tr.get('telemetry_valid') and tr.get('last_rx_at')!=self.last_rx and m['target_rpm'] is not None and m['measured_rpm'] is not None:
            self.last_rx=tr['last_rx_at'];self.history.append((self.last_rx,m['target_rpm'],m['measured_rpm']))
        self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        if not self.history:
            p.setPen(QColor('#aab8c8'));p.drawText(self.rect(),Qt.AlignCenter,ko('NO MOTOR MEASUREMENTS RECEIVED\nTarget and measured RPM history appears after valid ESP32 telemetry.'));return
        end=self.history[-1][0]
        points=[r for r in self.history if r[0]>=end-30]
        row=self.height()/3
        for motor in range(3):
            y=motor*row
            limit=max(1,max(abs(v) for _,a,b in points for v in (a[motor],b[motor])))
            p.setPen(QColor('#aab8c8'));p.drawText(8,int(y+17),ko(f'M{motor+1} RPM  +/-{limit:.1f}   target blue / measured green'))
            for column,color in [(1,'#62a5ff'),(2,'#4cdaa3')]:
                p.setPen(QPen(QColor(color),1.7));previous=None
                for point in points:
                    current=QPointF(10+(point[0]-(end-30))/30*(self.width()-20),y+row*.60-point[column][motor]/limit*row*.28)
                    if previous is not None:p.drawLine(previous,current)
                    previous=current


def scroll(widget):
    area=QScrollArea();area.setWidgetResizable(True);area.setWidget(widget);area.setFrameShape(QScrollArea.NoFrame)
    return area


class Dashboard(QMainWindow):
    def __init__(self,config,hardware=False,preview=False,folder=None,stream=None,engine=None):
        super().__init__()
        # Restricted/offscreen Windows sessions can expose an empty font database.
        if not QFontDatabase.families():
            for name in ('segoeui.ttf','segoeuib.ttf','malgun.ttf','malgunbd.ttf'):
                font=Path('C:/Windows/Fonts')/name
                if font.exists():QFontDatabase.addApplicationFont(str(font))
        self.setWindowTitle('D.I.G. | 옴니 로봇 중앙 제어')
        app=QApplication.instance()
        if not hasattr(app,'_korean_translator'):
            app._korean_translator=QTranslator(app)
            app._korean_translator.load('qtbase_ko',QLibraryInfo.path(QLibraryInfo.TranslationsPath))
            app.installTranslator(app._korean_translator)
        self.resize(1320,920);self.setMinimumSize(940,680)
        root=Path(__file__).resolve().parents[2]
        self.connection_file=root/'settings'/'connection.json'
        self.radar_file=root/'settings'/'radar.json'
        source='SYNTHETIC_PREVIEW' if preview else 'LIVE_CAMERA'
        self.background_control=engine is None
        self.engine=engine or DashboardEngine(config,folder or root/'logs',source)
        self.engine.central_ui=True
        self.stream=stream or (PreviewStream() if preview else VisionStream(config))
        self.last_image_at=None;self.last_data=None;self.closing=False
        self.held_direction=None;self.pending_direct=None
        self.pending_jog=None
        self.network_probe=NetworkProbe();self.last_diagnostic_report=None
        self.build_ui()
        self.apply_style()
        if hardware:
            try:self.engine.connect_robot(config['communication']['host'])
            except Exception as exc:
                self.engine.notice=f'HARDWARE CONNECTION FAILED: {exc}'
                self.engine.c.halt('FAULT','HARDWARE_CONNECTION_FAILED',True)
            self.tabs.setCurrentIndex(self.motor_tab_index)
        self.stream.start()
        if self.background_control:self.engine.start_control_loop(self.stream)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(50)

    def build_ui(self):
        central=QWidget();outer=QVBoxLayout(central);outer.setContentsMargins(16,12,16,12);outer.setSpacing(10)
        top=QHBoxLayout();title=QLabel('D.I.G. 중앙 제어');title.setObjectName('title')
        self.mode=QLabel(ko('SIMULATION'));self.mode.setObjectName('mode')
        self.state=QLabel(ko('STARTING'));self.arm_state=QLabel(ko('DISARMED'))
        self.estop=QPushButton(ko('EMERGENCY STOP   [SPACE]'));self.estop.setObjectName('estop');self.estop.setMinimumHeight(46)
        self.estop.clicked.connect(lambda:self.action('ESTOP'))
        self.release_control=QPushButton('정지 후 연결 해제')
        self.release_control.clicked.connect(self.disconnect)
        top.addWidget(title);top.addStretch();top.addWidget(self.mode);top.addWidget(self.state);top.addWidget(self.arm_state);top.addWidget(self.estop)
        top.addWidget(self.release_control)
        outer.addLayout(top)
        radar_row=QHBoxLayout()
        self.radar_status=QLabel('레이더 · ESP32 연결 대기')
        self.radar_status.setWordWrap(True)
        self.radar_rearm=QPushButton('레이더 해제 확인 · 새 이동 허용')
        self.radar_rearm.setEnabled(False)
        self.radar_rearm.clicked.connect(self.rearm_radar)
        radar_row.addWidget(self.radar_status,1)
        radar_row.addWidget(self.radar_rearm)
        outer.addLayout(radar_row)
        self.pipeline=QLabel(ko('Initializing camera and models in background...'));self.pipeline.setWordWrap(True)
        outer.addWidget(self.pipeline)
        actions=QGridLayout()
        for i,(label,action) in enumerate([('목표 확정','CONFIRM'),('준비 [A]','ARM'),('재개 [R]','RESUME'),('일시정지','PAUSE'),('목표 해제','CLEAR')]):
            button=QPushButton(label);button.setObjectName(action);button.clicked.connect(lambda checked=False,a=action:self.action(a));actions.addWidget(button,i//3,i%3)
        self.distance=QLabel(ko('Relative height target: --'));self.distance.setWordWrap(True)
        self.workspaces=QTabWidget();outer.addWidget(self.workspaces,1)
        self.tabs=QTabWidget()
        self.workspaces.addTab(self.tabs,'중앙제어')
        # The primary camera view stays large; long details scroll independently.
        split=self.hand_split=QSplitter(Qt.Horizontal)
        left=self.camera_column=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,4,0)
        self.camera=CameraView();self.camera.selected.connect(lambda id:self.engine.request('SELECT',id));ll.addWidget(self.camera,1)
        self.camera_status=QLabel(ko('Waiting for frames'));self.camera_status.setWordWrap(True);ll.addWidget(self.camera_status)
        self.hand_readout=QLabel();self.hand_readout.setWordWrap(True);ll.addWidget(self.hand_readout)
        self.tracking=Fields('TARGET / TRACKING',['People','Target ID','Confirmed / Lost','Confidence','Center (px)','Last seen / age (s)','Horizontal error','Relative height'])
        right=QWidget();rl=QVBoxLayout(right);rl.setContentsMargins(4,0,0,0)
        self.raw=Fields('01 / RAW GESTURE',['Hands','Raw labels / confidence / owner','Sample age (s)'])
        self.confirmed=Fields('02 / CONFIRMED GESTURE',['Current confirmation','Time / required (s)','Frames / required','Confidence threshold'])
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0)
        self.confirmed.layout().addRow(self.progress)
        self.applied=Fields('03 / APPLIED ACTION',['Applied this update','Last gesture action / age (s)','Rejection / hold reason'])
        self.safe=Fields('CONTROL / SAFETY',['State','Requested vx / vy / w','Safe vx / vy / w','Limits or stop applied','Final stop / status','Radar'])
        self.arrow=MotionArrow()
        split.addWidget(left);split.addWidget(right);split.setSizes([650,550])
        self.vision_text=QPlainTextEdit();self.vision_text.setReadOnly(True)
        motion=QWidget();ml=QVBoxLayout(motion)
        self.telemetry=Fields('ESP32 / TRANSPORT',['Connection','Receive validity / reason','수신 검증 상세 (validation_detail)','장치 Fault (telemetry.fault)','구동 준비','복구 상태 / 세대','Last RX / age (s)','Session / telemetry last_seq','일치 텔레메트리 지연 (ms)','Last successful UDP send','Telemetry seq match (not ACK)','ESP32 state / fault','ESP32 reported desired cmd','Stop transmission / feedback'])
        ml.addWidget(self.telemetry)
        self.motor_table=QTableWidget(3,6);self.motor_table.setHorizontalHeaderLabels([ko(s) for s in ['Motor','Target cm/s','Measured cm/s','Target RPM*','Measured RPM*','PWM / encoder']])
        self.motor_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.motor_table.setMaximumHeight(155)
        self.motor_table.setEditTriggers(QTableWidget.NoEditTriggers);ml.addWidget(self.motor_table)
        ml.addWidget(QLabel('* ESP32 RPM 우선 표시 · RPM 필드가 없으면 설정 바퀴 반지름으로 환산'))
        self.graph=MotorGraph();ml.addWidget(self.graph,1)
        tests=QWidget();tl=QVBoxLayout(tests)
        connect=QGroupBox('ESP32 연결');cl=QVBoxLayout(connect)
        row=QHBoxLayout();self.host=QLineEdit();self.host.setPlaceholderText(ko('ESP32 IPv4 address'))
        from python_controller.communication.motor_test import load_connection
        self.host.setText(ko(self.engine.cfg['communication']['host'] or load_connection(self.connection_file)))
        self.host.textChanged.connect(self.on_host_changed)
        self.authorize=QCheckBox(ko('Authorize UDP connection only (does not authorize motor motion)'))
        connect_button=self.connect_button=QPushButton('ESP32 연결');connect_button.clicked.connect(self.connect_hardware)
        save_ip=QPushButton(ko('Save IP'));save_ip.clicked.connect(self.save_ip)
        row.addWidget(self.host,1);row.addWidget(save_ip);row.addWidget(connect_button);cl.addLayout(row)
        radar_settings=QHBoxLayout()
        self.radar_host=QLineEdit(self.engine.cfg['radar']['source_ip'])
        self.radar_host.setPlaceholderText('레이더 Raspberry Pi IPv4')
        self.radar_host.setAccessibleName('레이더 Raspberry Pi IPv4')
        self.save_radar_button=QPushButton('Pi 주소 저장')
        self.save_radar_button.clicked.connect(self.save_radar_ip)
        radar_settings.addWidget(QLabel('레이더 Pi'))
        radar_settings.addWidget(self.radar_host,1)
        radar_settings.addWidget(self.save_radar_button)
        cl.addLayout(radar_settings)
        outer.insertWidget(1,connect)
        self.log_path=QLabel(ko(str(self.engine.logger.directory) if self.engine.logger else 'Logging disabled (test)'));self.log_path.setWordWrap(True)
        tl.addWidget(QLabel(ko('JSONL session log (10 Hz + operator events; no camera images):')));tl.addWidget(self.log_path)
        self.test_table=QTableWidget(7,3);self.test_table.setHorizontalHeaderLabels([ko(s) for s in ['Validation step','Operator result','Evidence / notes']])
        self.test_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.test_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.test_table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i,row in enumerate(self.engine.book.rows):
            for j,value in enumerate((row['test'],row['result'],row['notes'])):self.test_table.setItem(i,j,QTableWidgetItem(ko(value)))
        tl.addWidget(self.test_table)
        controls=QHBoxLayout();self.test_index=QComboBox();add_choices(self.test_index,self.engine.book.NAMES)
        self.test_result=QComboBox();add_choices(self.test_result,['NOT TESTED','PASS','FAIL'])
        self.test_notes=QLineEdit();self.test_notes.setPlaceholderText(ko('Observed result / evidence (required for PASS or FAIL)'))
        self.performed=QCheckBox(ko('I performed this step'))
        record=QPushButton(ko('Record result'));record.clicked.connect(self.record_result)
        controls.addWidget(self.test_index);controls.addWidget(self.test_result);controls.addWidget(self.test_notes,1)
        tl.addLayout(controls);tl.addWidget(self.performed);tl.addWidget(record)
        guide=QLabel(ko('1 Camera: image / latency.  2 Tracking: select, move, hide.  3 Gestures: palm, fist, up, down.\n'
                    '4 Follow: left/right/near/far/lost.  5 ESP32: TX, telemetry seq/echo, feedback, disconnect.\n'
                    '6 Lifted wheels: directions, encoders, RPM, STOP.  7 Floor drive: separate user approval and controlled space.\n'
                    'Recording a result does not execute a motor test. All tests start NOT TESTED. No rear radar is connected.'))
        guide.setWordWrap(True);tl.addWidget(guide);tl.addStretch()
        bench=QWidget();bl=QVBoxLayout(bench)
        diagnostic_box=QGroupBox('연결 및 통신 진단 · 모터를 움직이지 않습니다');dl=QVBoxLayout(diagnostic_box)
        diagnostic_buttons=QHBoxLayout()
        self.network_check=QPushButton('네트워크·수신 포트 점검');self.network_check.clicked.connect(self.run_network_check)
        self.save_diagnosis=QPushButton('진단 보고서 저장 (JSON·Markdown)');self.save_diagnosis.clicked.connect(self.export_diagnosis)
        diagnostic_buttons.addWidget(self.network_check);diagnostic_buttons.addWidget(self.save_diagnosis)
        dl.addLayout(diagnostic_buttons)
        self.network_status=QLabel('네트워크 점검은 아직 수행하지 않았습니다. IP 저장과 UDP 연결은 별도입니다.');self.network_status.setWordWrap(True);dl.addWidget(self.network_status)
        self.diagnostic_steps=QTableWidget(6,3);self.diagnostic_steps.setHorizontalHeaderLabels(['진단 단계','결과','관측 근거 및 다음 확인'])
        self.diagnostic_steps.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.diagnostic_steps.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.diagnostic_steps.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.diagnostic_steps.setEditTriggers(QTableWidget.NoEditTriggers);self.diagnostic_steps.setMinimumHeight(290);dl.addWidget(self.diagnostic_steps)
        dl.addWidget(QLabel('현재 IP는 시리얼 모니터 115200bps에서 STATUS로 확인하세요. 수동 모드라면 먼저 STOP → AUTO → STATUS를 확인합니다.'))
        dl.addWidget(QLabel('송수신 상세 원문: 프로토콜 식별자·JSON 필드명은 호환성을 위해 유지하며, 자격 증명 필드는 제외합니다.'))
        self.communication_text=QPlainTextEdit();self.communication_text.setReadOnly(True);self.communication_text.setMaximumHeight(160);dl.addWidget(self.communication_text)
        warning=QLabel('방향 버튼을 누르는 동안 이동합니다. 손을 떼면 정지합니다.')
        warning.setWordWrap(True);bl.addWidget(warning)
        self.bench_state=Fields('구동 및 정지 상태',
            ['Source','Status / reason','Command processing evidence','Stop feedback','최근 장치 오류','Current / last trial'])
        settings=QHBoxLayout()
        self.manual_arm=QPushButton('바퀴 고정 확인 및 실물 직접 제어 활성화')
        self.manual_arm.clicked.connect(self.enable_motor_test)
        self.heartbeat_button=QPushButton(ko('Stationary heartbeat check'));self.heartbeat_button.setEnabled(False);self.heartbeat_button.clicked.connect(self.probe_heartbeat)
        settings.addWidget(self.manual_arm);settings.addWidget(self.heartbeat_button);settings.addStretch()
        buttons=QGridLayout();self.test_buttons={}
        wheel_buttons=QHBoxLayout();self.wheel_buttons={}
        for label,name,row,col in [('Forward (+vx)','FORWARD',0,1),('Backward (-vx)','BACKWARD',2,1),('Left (+vy)','LEFT',1,0),
                                   ('Right (-vy)','RIGHT',1,2),('Turn left (+w)','TURN_LEFT',0,0),('Turn right (-w)','TURN_RIGHT',0,2),
                                   ('M1','M1',3,0),('M2','M2',3,1),('M3','M3',3,2)]:
            button=QPushButton(ko(label));button.setEnabled(False);button.setMinimumHeight(42)
            button.pressed.connect(lambda n=name:self.start_motor_test(n))
            button.released.connect(self.end_motor_hold)
            if name in ('M1','M2','M3'):
                self.wheel_buttons[name]=button;wheel_buttons.addWidget(button)
            else:
                self.test_buttons[name]=button;buttons.addWidget(button,row,col)
        stop_button=QPushButton(ko('STOP'));stop_button.setObjectName('estop');stop_button.clicked.connect(lambda:self.action('PAUSE'));buttons.addWidget(stop_button,1,1)
        drive_columns=QHBoxLayout()
        jog_box=QGroupBox('직접 구동');jog_box.setLayout(buttons)
        drive_columns.addWidget(jog_box, 4)
        motor_box=QGroupBox('바퀴 텔레메트리');motor_layout=QVBoxLayout(motor_box)
        drive_columns.addWidget(motor_box, 6)
        bl.addLayout(drive_columns)
        self.bench_motors=QTableWidget(3,5);self.bench_motors.setHorizontalHeaderLabels(['바퀴','목표 RPM','실측 RPM','PWM','엔코더'])
        self.bench_motors.verticalHeader().hide()
        self.bench_motors.verticalHeader().setDefaultSectionSize(27)
        self.bench_motors.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.bench_motors.setMaximumHeight(150)
        self.bench_motors.setEditTriggers(QTableWidget.NoEditTriggers);motor_layout.addWidget(self.bench_motors)
        motor_layout.addWidget(self.bench_state)
        bl.addStretch()
        trial_page=QWidget();record_layout=QVBoxLayout(trial_page)
        record_layout.addWidget(QLabel('바퀴별 시험 · 세 바퀴를 공중에 고정한 상태에서 확인하세요.'))
        record_layout.addLayout(wheel_buttons)
        self.turn_trial=QPushButton('저속 좌회전 0.5초 시험')
        self.turn_trial.setEnabled(False)
        self.turn_trial.clicked.connect(self.start_turn_trial)
        record_layout.addWidget(self.turn_trial)
        record_layout.addLayout(settings)
        self.trial_selection=QComboBox();self.trial_selection.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);record_layout.addWidget(self.trial_selection)
        observations=QGridLayout();self.direction_observations=[]
        for motor in range(3):
            box=QComboBox();add_choices(box,['NOT TESTED','MATCHES EXPECTED','OPPOSITE','NO ROTATION'])
            observations.addWidget(QLabel(ko(f'M{motor+1} visual result')),motor,0);observations.addWidget(box,motor,1);self.direction_observations.append(box)
        record_layout.addLayout(observations)
        self.motor_notes=QLineEdit();self.motor_notes.setPlaceholderText(ko('Physical observations / wiring / encoder direction notes; do not infer body travel from encoder counts'))
        record_layout.addWidget(self.motor_notes)
        self.hardware_checks=[]
        for label in ['모터 전원 전압을 실제 측정함','드라이버 STBY와 공통 GND를 확인함','모터 핀·엔코더 배선·풀업을 확인함','물리 전원 차단 수단을 확인함']:
            check=QCheckBox(label);self.hardware_checks.append(check);record_layout.addWidget(check)
        record_motor=QPushButton(ko('Record visual observations for selected completed trial'));record_motor.clicked.connect(self.record_motor_observations);record_layout.addWidget(record_motor)
        self.trial_text=QPlainTextEdit();self.trial_text.setReadOnly(True);record_layout.addWidget(self.trial_text)
        self.motor_tab_index=self.tabs.addTab(scroll(bench),'직접 제어')
        self.follow_panel=FollowControlPanel(self.engine,self.host,self.disconnect)
        hand_controls=self.build_hand_control()
        rl.addWidget(scroll(hand_controls))
        follow_flow=QWidget();follow_layout=QVBoxLayout(follow_flow)
        follow_layout.addLayout(actions);follow_layout.addWidget(self.distance);follow_layout.addWidget(self.follow_panel)
        follow_layout.addWidget(self.tracking);follow_layout.addStretch()
        self.follow_tab_index=self.tabs.addTab(split,'손동작 제어')
        self.people_split=QSplitter(Qt.Horizontal);self.people_split.addWidget(scroll(follow_flow))
        self.people_tab_index=self.tabs.addTab(self.people_split,'사람 추종')
        for action in ('CONFIRM','ARM','RESUME'):
            central.findChild(QPushButton,action).hide()
        self.language_panel=NaturalControlPanel(self.engine,self.host,lambda:self.action('ESTOP'))
        self.language_tab_index=self.tabs.addTab(scroll(self.language_panel),'자연어 제어')
        self.diagnosis_pages=QTabWidget()
        self.diagnosis_pages.addTab(scroll(diagnostic_box),'연결')
        self.diagnosis_pages.addTab(scroll(motion),'모터·텔레메트리')
        vision_page=QWidget();vision_layout=QVBoxLayout(vision_page)
        for widget in (self.vision_text,self.raw,self.confirmed,self.applied,self.safe,self.arrow):
            vision_layout.addWidget(widget)
        self.vision_text.setMinimumHeight(280)
        self.diagnosis_pages.addTab(scroll(vision_page),'카메라·추론')
        self.diagnosis_pages.addTab(scroll(tests),'검증·로그')
        self.diagnosis_pages.addTab(scroll(trial_page),'바퀴 시험 기록')
        self.diagnostic_tab_index=self.workspaces.addTab(self.diagnosis_pages,'테스트·진단')
        self.follow_panel.settings.setParent(None)
        self.diagnosis_pages.addTab(scroll(self.follow_panel.settings),'인식·추종 설정')
        recovery_page=QWidget();recovery_layout=QVBoxLayout(recovery_page)
        self.recovery_fields=Fields('엔코더 오류·통신 수명 진단',[
            '수신 검증 상세 (validation_detail)','장치 Fault (telemetry.fault)','출력 정지 확인','구동 준비','복구 상태 / 세대',
            '프로토콜 / 연결 생존','ESP32 적용 정책','정책 변경 지원','ESP32 적용 heartbeat 만료 (ms)',
            'ESP32 적용 속도 TTL (ms)','PC heartbeat 주기 / 실제 간격 (ms)','상위 제어 상태 나이 (s)',
            'heartbeat_age_ms','velocity_age_ms','goal_elapsed_ms','controller_alive','motion_permit',
            'active_motion_id','execution_token','fault_latched','fault_motor_mask','fault_dt_us','fault_delta_limit',
            'fault_delta_count','fault_previous_count','fault_current_count','fault_pwm_before',
            'encoder_warning','encoder_good_samples','bad_samples','encoder_recent_bad',
            'fault_sample_ms','last_fault','last_fault_at_ms'])
        recovery_layout.addWidget(self.recovery_fields)
        policy_row=QHBoxLayout();self.encoder_policy=QComboBox();add_choices(self.encoder_policy,['STRICT','FILTERED_TEST'])
        self.encoder_policy.setAccessibleName('요청할 엔코더 시험 정책')
        self.encoder_policy_apply=QPushButton('정책 적용 요청');self.encoder_policy_apply.clicked.connect(self.apply_encoder_policy)
        self.recover_button=QPushButton('정지 후 복구·재동기화');self.recover_button.clicked.connect(self.recover_robot)
        policy_row.addWidget(self.encoder_policy);policy_row.addWidget(self.encoder_policy_apply);policy_row.addWidget(self.recover_button)
        recovery_layout.addLayout(policy_row)
        self.recovery_notice=QLabel('실제 적용 정책과 수명 설정은 ESP32 응답으로 확인합니다. 복구 후에는 새 입력이 필요합니다.')
        self.recovery_notice.setWordWrap(True);recovery_layout.addWidget(self.recovery_notice);recovery_layout.addStretch()
        self.diagnosis_pages.addTab(scroll(recovery_page),'엔코더·복구')
        self.tabs.currentChanged.connect(self.select_page_mode)
        self.workspaces.currentChanged.connect(self.select_page_mode)
        self.diagnosis_pages.currentChanged.connect(self.select_page_mode)
        self.authorize.hide()
        self.setCentralWidget(central)
        QApplication.instance().installEventFilter(self)

    def build_hand_control(self):
        box=QGroupBox('제스처와 방향');form=QFormLayout(box)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.hand_summary=QLabel('실물 구동 대기');self.hand_summary.setWordWrap(True)
        form.addRow(self.hand_summary)
        self.hand_message=QLabel();self.hand_message.setWordWrap(True)
        self.hand_preview=HandDrive()
        self.hand_host=QLineEdit(self.host.text())
        self.hand_host.setPlaceholderText('ESP32 IPv4')
        self.hand_host.textChanged.connect(self.host.setText)
        self.host.textChanged.connect(self.hand_host.setText)
        self.hand_host.hide()
        self.hand_confidence=QDoubleSpinBox();self.hand_confidence.setRange(.50,.99)
        self.hand_confidence.setSingleStep(.05);self.hand_confidence.setValue(self.engine.hand.confidence)
        self.hand_frames=QSpinBox();self.hand_frames.setRange(2,8);self.hand_frames.setValue(self.engine.hand.min_frames)
        self.hand_hold=QDoubleSpinBox();self.hand_hold.setRange(.10,1);self.hand_hold.setSingleStep(.05)
        self.hand_hold.setValue(self.engine.hand.hold);self.hand_hold.setSuffix(' 초')
        self.hand_mapping={}
        for gesture,direction in DEFAULT_MAPPING.items():
            if gesture=='Closed_Fist':
                form.addRow(ko(gesture),QLabel('즉시 정지'))
                continue
            choice=QComboBox();add_choices(choice,DIRECTIONS);choice.setCurrentIndex(choice.findData(direction))
            choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);choice.setMinimumContentsLength(7)
            self.hand_mapping[gesture]=choice;form.addRow(ko(gesture),choice)
        form.addRow('최소 신뢰도',self.hand_confidence)
        form.addRow('최소 연속 프레임',self.hand_frames)
        form.addRow('최소 유지시간',self.hand_hold)
        guide=QLabel('손동작이 유지되는 동안 구동합니다. 손을 내리거나 주먹을 쥐면 정지합니다.')
        guide.setWordWrap(True);form.addRow(guide)
        self.hand_enable=QPushButton('손동작 시작 / 설정 적용')
        self.hand_enable.clicked.connect(self.enable_hand_drive)
        form.addRow(self.hand_enable);form.addRow(self.hand_message)
        return box

    def apply_encoder_policy(self):
        if not self.encoder_policy_apply.isEnabled():return
        try:self.engine.set_encoder_policy(self.encoder_policy.currentData())
        except (ValueError,OSError) as exc:
            self.recovery_notice.setText(f'정책 요청 실패: {exc}');return
        self.recovery_notice.setText('정책 변경을 요청했습니다. ESP32 적용 정책 응답을 확인하세요.')

    def recover_robot(self):
        if not self.recover_button.isEnabled():return
        try:self.engine.recover_robot()
        except (ValueError,OSError) as exc:
            self.recovery_notice.setText(f'복구 요청 실패: {exc}');return
        self.held_direction=None;self.pending_direct=None
        self.pending_jog=None
        self.recovery_notice.setText('이전 입력을 폐기하고 STOP 전용 동기화를 요청했습니다. 장치 Fault 해제와 구동 준비는 별도로 확인합니다.')

    def rearm_radar(self):
        self.end_motor_hold()
        try:
            self.engine.rearm_radar()
        except (OSError, ValueError) as exc:
            self.warning('레이더 이동 허용 실패', str(exc))

    def update_recovery(self,tr,td):
        td=td or {}
        missing='미수신·미검증 (구형 펌웨어는 미지원)'
        blocked=movement_block(tr)
        ready=bool(tr.get('ready') and not blocked)
        recovery=f'{text(tr.get("recovery_state"))} / {text(tr.get("recovery_generation"))}'
        support=tr.get('supported_test_profile') is True
        values={
            '수신 검증 상세 (validation_detail)':tr.get('validation_detail'),
            '장치 Fault (telemetry.fault)':td.get('fault'),
            '출력 정지 확인':'출력 정지 확인됨' if tr.get('stop_verified') else '출력 정지 미검증',
            '구동 준비':'새 입력 가능' if ready else f'이동 차단 · {ko(blocked or tr.get("reason") or "UNKNOWN")}',
            '복구 상태 / 세대':recovery,
            '프로토콜 / 연결 생존':f'v{text(tr.get("protocol_version"))} / {text(tr.get("link_alive"))}',
            'ESP32 적용 정책':td.get('encoder_policy',missing),
            '정책 변경 지원':'지원 확인' if support else '미지원 또는 미확인',
            'ESP32 적용 heartbeat 만료 (ms)':td.get('heartbeat_timeout_ms',td.get('applied_heartbeat_timeout_ms',missing)),
            'ESP32 적용 속도 TTL (ms)':td.get('velocity_ttl_ms',td.get('applied_velocity_ttl_ms',missing)),
            'PC heartbeat 주기 / 실제 간격 (ms)':f'{text(tr.get("heartbeat_period_ms"))} / {text(tr.get("heartbeat_gap_ms"))}',
            '상위 제어 상태 나이 (s)':tr.get('controller_age_s')}
        for key in ('heartbeat_age_ms','velocity_age_ms','goal_elapsed_ms','controller_alive','motion_permit',
                    'active_motion_id','execution_token','fault_latched','fault_motor_mask','fault_dt_us','fault_delta_limit',
                    'fault_delta_count','fault_previous_count','fault_current_count','fault_pwm_before',
                    'encoder_warning','encoder_good_samples','bad_samples','encoder_recent_bad',
                    'fault_sample_ms','last_fault','last_fault_at_ms'):
            values[key]=td.get(key,td.get('encoder_bad_samples',missing) if key=='bad_samples' else missing)
        self.recovery_fields.update_values(values)
        shared=self.engine.shared_session
        idle=bool(shared and not shared.active and not self.engine.language.busy and
                  self.engine.release_future is None and not self.engine.pending_mode)
        can_apply=bool(idle and support and tr.get('stop_verified') and not tr.get('session_lost') and
                       tr.get('recovery_state') not in ('SYNC_STOP','SESSION_CONFLICT'))
        self.encoder_policy.setEnabled(can_apply);self.encoder_policy_apply.setEnabled(can_apply)
        self.recover_button.setEnabled(bool(idle and tr.get('recovery_state') not in ('SYNC_STOP','SESSION_CONFLICT')))

    def hand_settings(self):
        return dict(confidence=self.hand_confidence.value(),frames=self.hand_frames.value(),
                    hold=self.hand_hold.value(),
                    mapping={'Closed_Fist':'STOP',**{name:box.currentData() for name,box in self.hand_mapping.items()}})

    def enable_hand_drive(self):
        try:
            self.engine.configure_hand(**self.hand_settings())
            self.hand_message.clear()
        except (OSError,ValueError) as exc:
            self.hand_message.setText(str(exc))

    def eventFilter(self,watched,event):
        if watched is self and event.type() in (QEvent.WindowDeactivate, QEvent.Hide):
            self.end_motor_hold()
        if event.type()==QEvent.KeyPress and isinstance(watched,QWidget) and (watched is self or self.isAncestorOf(watched)):
            editing=isinstance(QApplication.focusWidget(),(QLineEdit,QPlainTextEdit,QComboBox,QTableWidget))
            if event.key()==Qt.Key_Space:
                if QApplication.focusWidget() is self.language_panel.command and not self.engine.language.executing:
                    return super().eventFilter(watched,event)
                self.action('ESTOP')
                return not isinstance(QApplication.focusWidget(),(QLineEdit,QPlainTextEdit))
            if not editing and not event.modifiers() & (Qt.ControlModifier|Qt.AltModifier|Qt.MetaModifier):
                action={Qt.Key_E:'ESTOP',Qt.Key_A:'ARM',Qt.Key_R:'RESUME',Qt.Key_P:'PAUSE',Qt.Key_C:'CLEAR',Qt.Key_Return:'CONFIRM',Qt.Key_Enter:'CONFIRM'}.get(event.key())
                if action in ('ARM','RESUME','CLEAR','CONFIRM') and (self.workspaces.currentIndex()!=0 or self.tabs.currentIndex()!=self.people_tab_index):
                    action=None
                if action:
                    if not event.isAutoRepeat():self.action(action)
                    return True
                if event.key() in (Qt.Key_Escape,Qt.Key_Q):self.close();return True
        return super().eventFilter(watched,event)

    def on_host_changed(self, host):
        if host.strip() != self.engine.language_authorized_host:
            self.engine.language_authorized_host=None

    def action(self,action):
        if action in ('ESTOP','PAUSE'):
            self.pending_jog=None
        if action=='ESTOP':
            self.held_direction=None
            self.pending_direct=None
        self.engine.request(action)
        self.language_panel.refresh()
        if action=='ESTOP':self.pipeline.setText(ko('EMERGENCY STOP LATCHED / explicit re-arm required'))

    def warning(self,title,message):
        original=str(message);translated=ko(original)
        if translated==original and not any('가'<=c<='힣' for c in translated):
            translated='작업을 완료하지 못했습니다. 입력·연결·승인 상태를 확인하세요.\n세부 오류 원문: '+original
        QMessageBox.warning(self,ko(title),translated)

    def run_network_check(self):
        try:
            host=self.host.text().strip()
            if self.engine.client and host!=self.engine.client.host:
                raise ValueError('현재 연결 주소와 입력 주소가 다릅니다. 먼저 연결을 해제하고 새 주소를 점검하세요.')
            self.network_probe.start(host,bool(self.engine.client and self.engine.client.snapshot().get('rx_bound')))
        except Exception as exc:self.warning('네트워크 점검 시작 실패',str(exc))

    def export_diagnosis(self):
        try:
            if not self.last_diagnostic_report:raise ValueError('먼저 진단 상태를 갱신하세요.')
            report=dict(self.last_diagnostic_report)
            report.update(program_mode=self.engine.mode,evidence_source=self.engine.source,
                          operator_hardware_checks={c.text():('OPERATOR_CHECKED' if c.isChecked() else 'NOT TESTED') for c in self.hardware_checks})
            folder=self.engine.logger.directory if self.engine.logger else Path(__file__).resolve().parents[2]/'logs'
            path=save_report(folder,report)
            self.engine.notice=f'진단 보고서를 저장했습니다: {path}'
        except Exception as exc:self.warning('진단 보고서 저장 실패',str(exc))

    def connect_hardware(self):
        try:
            self.engine.connect_robot(self.host.text().strip())
            self.select_page_mode()
        except Exception as exc:self.warning('Hardware connection refused',str(exc))
        self.language_panel.refresh()

    def select_page_mode(self, *_):
        self.end_motor_hold()
        if self.tabs.currentIndex() in (self.follow_tab_index,self.people_tab_index):
            split=self.people_split if self.tabs.currentIndex()==self.people_tab_index else self.hand_split
            split.insertWidget(0,self.camera_column)
            split.setSizes([700,470])
        mode=('MOTOR_TEST' if self.diagnosis_pages.currentIndex()==4 else None) if self.workspaces.currentIndex()==1 else {
            self.motor_tab_index:'DIRECT_CONTROL',self.follow_tab_index:'GESTURE_DIRECT',
            self.people_tab_index:'GESTURE_FOLLOW',self.language_tab_index:'LLM_COMMAND'}[self.tabs.currentIndex()]
        if mode is None:
            return
        try:
            self.engine.select_control_mode(mode)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc),3000)

    def disconnect(self):
        self.end_motor_hold();self.pending_direct=None
        self.engine.disconnect_async();self.authorize.setChecked(False)
        self.language_panel.refresh()
        self.follow_panel.refresh()

    def save_ip(self):
        try:
            from python_controller.communication.motor_test import save_connection
            save_connection(self.connection_file,self.host.text().strip())
            self.engine.notice='ESP32 IP saved locally; no network connection started'
        except Exception as exc:self.warning('IP not saved',str(exc))

    def save_radar_ip(self):
        try:
            self.engine.save_radar_source(self.radar_file, self.radar_host.text())
            self.radar_host.setText(self.engine.cfg['radar']['source_ip'])
        except (ValueError, OSError) as exc:
            self.warning('Pi 주소 저장 실패', str(exc))

    def enable_motor_test(self):
        try:
            if self.engine.shared_session:
                self.engine.select_control_mode('MOTOR_TEST')
            else:
                raise ValueError('먼저 상단에서 ESP32를 연결하세요.')
        except Exception as exc:self.warning('Motor test not enabled',str(exc))

    def probe_heartbeat(self):
        try:
            if not self.engine.shared_session:
                raise ValueError('먼저 상단에서 ESP32를 연결하세요.')
            self.engine.shared_session.stop('STATIONARY_PROBE')
        except Exception as exc:self.warning('Heartbeat not started',str(exc))

    def start_motor_test(self,direction):
        try:
            if self.held_direction is None:
                session = self.engine.shared_session
                if session and direction in self.test_buttons:
                    status = session.snapshot()
                    if self._jog_available(status, waiting=True) and not (status['stop_verified'] and status['ready']):
                        fault_at = (status.get('telemetry') or {}).get('last_fault_at_ms')
                        self.pending_jog = (direction, session, fault_at)
                        return
                self.engine.operator_alive()
                self.engine.begin_hold(direction)
                self.held_direction=direction
        except (OSError,ValueError) as exc:
            self.statusBar().showMessage(str(exc),3000)

    def start_turn_trial(self):
        try:
            self.engine.operator_alive()
            self.engine.begin_hold('TURN_LEFT', timed_trial=True)
        except (OSError,ValueError) as exc:
            self.engine.notice=str(exc)
            if self.engine.logger:
                self.engine.logger.write(dict(event='MOTOR_TEST_REJECTED',reason=str(exc)),True)
            self.warning('바퀴 시험 시작 실패',str(exc))

    def end_motor_hold(self):
        self.pending_jog=None
        if self.held_direction is not None:
            self.held_direction=None
            self.engine.end_hold()

    def _jog_available(self, transport, *, waiting=False):
        return bool(self.engine.client_owner=='DIRECT_CONTROL' and not self.engine.pending_mode and
                    not self.engine.release_future and not movement_block(transport) and
                    (transport.get('reason')=='HOLD_RELEASED' if waiting else
                     transport.get('ready') and transport.get('stop_verified')))

    def record_motor_observations(self):
        try:
            client=self.engine.shared_session or self.engine._manual_client()
            client.record_observations(self.trial_selection.currentData(),
                [box.currentData() for box in self.direction_observations],self.motor_notes.text())
        except Exception as exc:self.warning('Observations not recorded',str(exc))

    def record_result(self):
        index,result=self.test_index.currentIndex(),self.test_result.currentData()
        if result!='NOT TESTED' and not self.performed.isChecked():
            self.warning('Result not recorded','Confirm you actually performed this step.');return
        if result=='PASS' and index>=4 and (self.engine.mode!='HARDWARE' or self.engine.source!='LIVE_CAMERA'):
            self.warning('Result not recorded','A hardware PASS cannot be recorded from SIMULATION or synthetic preview.');return
        try:
            self.engine.book.record(index,result,self.test_notes.text(),self.engine.mode,
                                    'OPERATOR_RECORDED / '+self.engine.source)
            if self.engine.logger:
                self.engine.logger.save_tests(self.engine.book)
                self.engine.logger.write(dict(event='OPERATOR_TEST_RESULT',test_result=dict(self.engine.book.rows[index])),True)
            for j,value in enumerate((self.engine.book.rows[index]['test'],result,self.test_notes.text())):
                self.test_table.item(index,j).setText(ko(value))
            self.performed.setChecked(False)
        except Exception as exc:self.warning('Result not recorded',str(exc))

    def poll(self):
        try:
            if self.background_control:
                if self.engine.control_error:
                    raise RuntimeError(self.engine.control_error)
                self.engine.operator_alive()
                data,result=self.engine.render_state()
                if data is None:return
                latest_capture=getattr(self.stream,'latest_capture',None)
                capture=latest_capture() if latest_capture else None
                self.update_display(data,result,capture)
                return
            self.engine.finish_disconnect()
            self.stream.hand_only=self.engine.client_owner=='GESTURE_DIRECT'
            result=self.stream.latest()
            latest_capture=getattr(self.stream,'latest_capture',None)
            capture=latest_capture() if latest_capture else None
            health=dict(vision=self.stream.status,camera=getattr(self.stream,'camera_status',None))
            health['capture_age_ms']=max(0,(time.monotonic()-capture[1])*1000) if capture is not None else None
            tracking_error=getattr(self.stream,'tracking_error',None)
            health['tracking_error']=tracking_error
            pipeline_error=self.stream.error or self.engine.error
            if self.engine.active_control_mode in ('SIMULATION','GESTURE_FOLLOW'):
                pipeline_error=pipeline_error or tracking_error
            data=self.engine.tick(result,pipeline_error=pipeline_error,pipeline_health=health)
            self.stream.target_id=self.engine.c.target_id
            self.update_display(data,result,capture)
        except Exception as exc:
            self.action('ESTOP');self.pipeline.setText(ko(f'UI ERROR / STOP LATCHED: {type(exc).__name__}: {exc}'))

    def update_camera(self, d, result, capture, now):
        if result and d['perception_fresh']:
            frame_key=('inference',result['completed_at'])
            status=ko(f'People {d["detected_people"]} | FPS {text(d["fps"],1)} | Processing {text(d["processing_ms"],1)} ms | Frame age {text(d["frame_age_s"])} s')
            if frame_key!=self.last_image_at:
                self.camera.set_frame(result,d['target_id'])
        elif capture is not None and 0<=now-capture[1]<=self.engine.cfg['safety']['perception_timeout_s']:
            image, timestamp, _ = capture
            frame_key=('raw',timestamp)
            if frame_key!=self.last_image_at:
                self.camera.show_capture(image)
            detail=(d.get('pipeline_health') or {}).get('tracking_error') or ko(self.stream.status)
            status=f'원본 영상 수신 중 · 인식 결과 대기 · {detail}'
        else:
            frame_key=None
            status='카메라 오류' if (d.get('pipeline_health') or {}).get('camera')=='CAMERA_ERROR' else '새 카메라 프레임 대기 중'
            if self.last_image_at is not None:
                self.camera.clear_capture()
        self.last_image_at=frame_key
        self.camera_status.setText(status)

    def update_display(self,d,result,capture=None):
        self.last_data=d;now=time.monotonic();tr=d['transport'];g=d['gesture'];v=d['vision']
        radar = tr.get('radar') or d.get('radar')
        if radar:
            if radar['blocked']:
                self.held_direction = self.pending_jog = None
            reason = ko(radar['reason'])
            action = '이동 차단' if radar['blocked'] else '새 입력 허용'
            proof = ('ESP32 정지 확인' if tr.get('stop_verified') else 'ESP32 정지 미확인')
            age_ms = None if radar['age_s'] is None else radar['age_s']*1000
            self.radar_status.setText(
                f"레이더 {radar['state']} · {action} · {reason} · 수신 경과 {text(age_ms, 0)} ms · {proof}")
            self.radar_rearm.setEnabled(bool(self.engine.shared_session and radar['blocked'] and
                radar['state']=='CLEAR' and tr.get('stop_verified') and not self.engine.language.executing))
        else:
            self.radar_status.setText('레이더 · ESP32 연결 대기')
            self.radar_rearm.setEnabled(False)
        generation=tr.get('recovery_generation')
        if generation is not None:
            if generation!=getattr(self,'_displayed_recovery_generation',generation):
                self.held_direction=None;self.pending_direct=None
                self.pending_jog=None
            self._displayed_recovery_generation=generation
        manual=d.get('control_source')=='MANUAL_MOTOR_TEST'
        central=d.get('control_source') in ('DIRECT_CONTROL','MOTOR_TEST')
        hand=d.get('control_source')=='GESTURE_DIRECT'
        hand_state=d.get('hand_control')
        natural=self.engine.language.executing
        self.language_panel.refresh()
        self.follow_panel.refresh(tr)
        mode_label={'LLM_COMMAND':'실제 장치 · 자연어 시험',
                    'MANUAL_MOTOR_TEST':'실제 장치 · 수동 시험',
                    'DIRECT_CONTROL':'실제 장치 · 직접 구동',
                    'MOTOR_TEST':'실제 장치 · 바퀴 시험',
                    'GESTURE_DIRECT':'실제 장치 · 손동작 직접 구동',
                    'GESTURE_FOLLOW':'실제 장치 · 손동작 추종'}.get(self.engine.active_control_mode,'시뮬레이션')
        self.mode.setText(mode_label+(' · 합성 (SYNTHETIC)' if self.engine.source!='LIVE_CAMERA' else ''))
        self.mode.setProperty('hardware',natural or d['program_mode']=='HARDWARE');self.mode.style().unpolish(self.mode);self.mode.style().polish(self.mode)
        state=(d['motor_test']['status'] if manual else '손동작 구동' if hand else
               d['state'] if self.tabs.currentIndex()==self.people_tab_index or central else '장치 미연결')
        self.state.setText('자연어 실행 중' if natural else MEANINGS.get(state,state))
        if self.engine.client and movement_block(tr):self.state.setText(ko(movement_block(tr)))
        self.arm_state.setText('실행 승인' if natural else ko(('MANUAL ENABLED' if manual else 'ARMED') if d['armed'] else 'DISARMED'))
        releasing=self.engine.release_future is not None
        self.release_control.setEnabled(not releasing and bool(self.engine.client or natural or self.engine.language_authorized_host))
        self.release_control.setText('정지 응답 확인 중' if releasing else '정지 후 연결 해제')
        self.host.setEnabled(not self.engine.client and not natural)
        radar_editable=not self.engine.client and not releasing and not natural
        self.radar_host.setEnabled(radar_editable)
        self.save_radar_button.setEnabled(radar_editable)
        self.connect_button.setEnabled(not self.engine.client and not natural and self.engine.source=='LIVE_CAMERA')
        self.hand_host.setEnabled(self.host.isEnabled())
        self.hand_enable.setEnabled(not natural and not (self.engine.client and self.engine.client.active))
        for widget in (*self.hand_mapping.values(),self.hand_confidence,self.hand_frames,self.hand_hold):widget.setEnabled(not hand)
        if not hand and d['frame_at'] is not None:
            options=self.hand_settings()
            preview=self.hand_preview
            if (preview.confidence,preview.min_frames,preview.hold,preview.mapping)!=tuple(options.values()):
                self.hand_preview=HandDrive(**options)
            hand_state=self.hand_preview.observe(v,d['frame_at'],now,self.engine.cfg['safety']['perception_timeout_s'],d['perception_fresh'])
        if hand_state:
            reason=hand_state['reason']
            if (hand or self.engine.client) and movement_block(tr):reason=movement_block(tr)
            reason={'WAITING_FOR_NEUTRAL_HAND':'정지 응답 확인 후 손을 내리거나 주먹을 쥐세요.',
                    'GESTURE_DRIVING':'구동 중', 'GESTURE_HOLD':'손동작 확인 중',
                    'WAITING_FOR_STOP':'새 STOP 응답 대기',
                    'GESTURE_STOP':'주먹 정지', 'NO_VALID_DRIVE_GESTURE':'구동 제스처 없음'}.get(reason,ko(reason))
            self.hand_summary.setText(f'{"" if hand else "미리보기 · "}{reason} · {ko(hand_state.get("label") or "없음")} · '
                                      f'신뢰도 {text(hand_state.get("confidence"),2)} · '
                                      f'{hand_state["elapsed"]:.2f}초 / {hand_state["frames"]}프레임 · {ko(hand_state["direction"])}')
        for action in ('ARM','RESUME'):
            self.findChild(QPushButton,action).setEnabled(not (manual or hand or natural))
        restriction='MULTIPLE PERSONS DETECTED - GESTURE CONTROL BLOCKED' if d['detected_people']>1 and not hand else ''
        perception='VISION DISCONNECTED FROM MOTORS' if manual else ('FRESH' if d['perception_fresh'] else 'STALE / STOP')
        self.pipeline.setText(ko(f'{self.stream.status} | {perception} | {d["stop_reason"]} | {restriction} | {d["notice"]}'))
        if self.engine.client or natural:
            fault=(d.get('telemetry') or tr.get('last_rx_json') or {}).get('fault')
            self.pipeline.setText(self.pipeline.text()+f' | 장치 Fault: {text(fault)} | 수신 검증: {text(tr.get("validation_detail"))}')
        self.distance.setText(ko(f'Relative height target: {d["desired_height_ratio"]:.2f} (uncalibrated)'))
        self.update_camera(d,result,capture,now)
        def age(timestamp):return max(0,now-timestamp) if timestamp is not None else None
        self.tracking.update_values({'People':d['detected_people'],'Target ID':d['target_id'],'Confirmed / Lost':f'{d["target_confirmed"]} / {d["target_lost"]}',
            'Confidence':d['detection_confidence'],'Center (px)':d['target_center'],'Last seen / age (s)':f'{text(d["target_last_seen"])} / {text(age(d["target_last_seen"]))}',
            'Horizontal error':d['horizontal_error'],'Relative height':d['relative_size']})
        raw_labels='\n'.join(f'{ko(r["name"])} / 신뢰도 {r["confidence"]:.2f} / 연결 목표 {text(r["associated_target"])}' for r in v['raw'])
        samples=', '.join(f'{ko(r["name"])} {r["confidence"]:.2f}' for r in v['raw']) or '미인식'
        self.hand_readout.setText(f'손 {v["hand_count"]}개 · {samples}')
        self.raw.update_values({'Hands':v['hand_count'],'Raw labels / confidence / owner':raw_labels or '없음','Sample age (s)':age(v.get('recognized_at'))})
        self.confirmed.update_values({'Current confirmation':g.get('confirmed') or 'NOT CONFIRMED',
            'Time / required (s)':f'{g.get("elapsed_s",0):.2f} / {g.get("required_s",0):.2f}',
            'Frames / required':f'{g.get("frames",0)} / {g.get("required_frames",0)}','Confidence threshold':self.engine.cfg['gesture']['confidence']})
        self.progress.setValue(round(g.get('progress',0)*100))
        self.applied.update_values({'Applied this update':g.get('applied_action') or 'NONE',
            'Last gesture action / age (s)':f'{text(g.get("last_applied_action"))} / {text(age(g.get("last_applied_at")))}',
            'Rejection / hold reason':g.get('rejection') or 'ACTION APPLIED'})
        def cmd(command):
            return 'UNKNOWN' if command is None else f'{command["vx_cm_s"]:+.2f} / {command["vy_cm_s"]:+.2f} cm/s / {command["w_rad_s"]:+.3f} rad/s'
        self.safe.update_values({'State':d['state'],'Requested vx / vy / w':cmd(d['requested']),'Safe vx / vy / w':cmd(d['safe']),
            'Limits or stop applied':d['limits_or_stop_applied'],'Final stop / status':d['stop_reason'],'Radar':'UNKNOWN / NOT CONNECTED'})
        self.arrow.set_data(d)
        safe_command=d['safe']
        prefix=mode_label
        self.statusBar().showMessage(ko(f'{prefix}  vx {safe_command["vx_cm_s"]:+.2f} cm/s | vy {safe_command["vy_cm_s"]:+.2f} cm/s | w {safe_command["w_rad_s"]:+.3f} rad/s   /   {d["stop_reason"]}'))
        language=self.engine.language
        td=d['telemetry'] or (language.last_feedback if natural else None)
        diagnostic=tr.get('session_diagnostic') or (language.session_diagnostic or language.session_info if natural else None) or self.engine.last_release or {}
        observed=diagnostic.get('telemetry') or tr.get('last_rx_json') or td or diagnostic
        processed=tr.get('processed_command') or {}
        latency=(tr['last_rx_at']-processed['host_sent_at'])*1000 if processed.get('host_sent_at') is not None and tr.get('last_rx_at') is not None else None
        if natural and language.last_echo_age_s is not None:
            latency=language.last_echo_age_s*1000
        stop_result=language.stop_result if not self.engine.client else None
        stop_verified=stop_result['stop_verified'] if stop_result is not None else tr.get('stop_verified')
        reported={key:td.get(key) for key in ('cmd_vx','cmd_vy','cmd_w')} if td else None
        connection=('자연어 실행 중 · 응답 수신' if td else '자연어 실행 중 · 응답 대기') if natural else (
            'CONNECTED / TELEMETRY VALID' if tr['connected'] else 'DISCONNECTED / NO VALID TELEMETRY')
        self.telemetry.update_values({'Connection':connection,
            'Receive validity / reason':f'{tr.get("telemetry_valid")} / {tr.get("telemetry_reason","NO_TELEMETRY")}',
            '수신 검증 상세 (validation_detail)':tr.get('validation_detail'),
            '장치 Fault (telemetry.fault)':observed.get('fault'),
            '구동 준비':'새 입력 가능' if tr.get('ready') and not movement_block(tr) else f'이동 차단 · {ko(movement_block(tr) or tr.get("reason") or "UNKNOWN")}',
            '복구 상태 / 세대':f'{text(tr.get("recovery_state"))} / {text(tr.get("recovery_generation"))}',
            'Last RX / age (s)':f'{text(tr.get("last_rx_at"))} / {text(tr.get("telemetry_age_s"))}',
            'Session / telemetry last_seq':f'TX {text(tr.get("session_id") or diagnostic.get("tx_session_id"))} · RX {text(observed.get("session_id") or observed.get("rx_session_id"))} · boot {text(observed.get("boot_id"))} · seq {text(observed.get("last_seq"))}',
            '일치 텔레메트리 지연 (ms)':latency,
            'Last successful UDP send':tr.get('last_tx'),'Telemetry seq match (not ACK)':tr.get('processed_command',tr.get('acknowledged_command')),
            'ESP32 state / fault':f'{text(observed.get("state") or observed.get("telemetry_state"))} / {text(observed.get("fault"))}',
            'ESP32 reported desired cmd':reported,
            'Stop transmission / feedback':f'stop seq {text(tr.get("stop_seq"))} / {"VERIFIED BY FRESH TELEMETRY" if stop_verified else "NOT VERIFIED"} | TX error {text(tr.get("tx_error"))}'})
        self.update_recovery(tr,observed)
        motors=motor_feedback(td,self.engine.cfg['communication']['wheel_radius_cm']) if natural else d['motors']
        for i in range(3):
            def at(key):return motors[key][i] if motors[key] is not None else None
            values=[f'M{i+1}',text(at('target_cm_s')),text(at('measured_cm_s')),text(at('target_rpm')),text(at('measured_rpm')),f'{text(at("pwm"))} / {text(at("encoder_count"))}']
            for j,value in enumerate(values):self.motor_table.setItem(i,j,QTableWidgetItem(ko(value)))
        self.graph.observe(d)
        self.update_motor_test(d)
        self.update_connection_diagnostics(d)
        if self.workspaces.currentIndex()==self.diagnostic_tab_index:
            self.vision_text.setPlainText(
                f'캡처 FPS {text(d["vision_metrics"].get("capture_fps"),1)} · 처리 FPS {text(d["fps"],1)} · '
                f'영상 나이 {text(d["frame_age_s"])}초 · 송신에서 일치 텔레메트리 수신까지 {text(latency,1)}ms\n'+
                json.dumps({key:d[key] for key in ['perception_fresh','target_id','vision_metrics','vision','gesture','hand_control','frame_at','vision_completed_at']},ensure_ascii=False,indent=2))

    def update_motor_test(self,d):
        tr=d['transport'];motor=d.get('motor_test')
        manual=d.get('control_source')=='MANUAL_MOTOR_TEST'
        trial=motor.get('trial') if motor else None
        history=motor.get('history',[]) if motor else []
        last=trial or (history[-1] if history else None)
        shared=self.engine.shared_session
        if shared:
            session_ok=not movement_block(tr)
            ready=not self.engine.pending_mode and not self.engine.release_future and tr.get('stop_verified',False) and tr.get('ready',False) and session_ok
            jog_ready = self._jog_available(tr)
            jog_wait = self._jog_available(tr, waiting=True)
            if self.pending_jog:
                direction, session, fault_at = self.pending_jog
                latest_fault_at = (tr.get('telemetry') or {}).get('last_fault_at_ms')
                if (session is not shared or latest_fault_at != fault_at or
                    not self.test_buttons[direction].isDown() or not (jog_ready or jog_wait)):
                    self.pending_jog=None
                elif jog_ready:
                    self.pending_jog=None
                    self.start_motor_test(direction)
            self.turn_trial.setEnabled(bool(ready and self.engine.client_owner=='MOTOR_TEST' and not shared.active and not trial))
            for direction,button in self.test_buttons.items():
                button.setEnabled(bool(jog_ready or jog_wait or self.engine.client_owner=='DIRECT_CONTROL' and
                                       self.held_direction==direction and shared.active and session_ok))
            for direction,button in self.wheel_buttons.items():
                button.setEnabled(bool(self.engine.client_owner=='MOTOR_TEST' and
                                       (ready or self.held_direction==direction and shared.active and session_ok)))
        else:
            self.pending_jog=None
            self.turn_trial.setEnabled(False)
            for button in (*self.test_buttons.values(),*self.wheel_buttons.values()):button.setEnabled(False)
        proof=manual and tr.get('stop_verified',False) and not trial
        complete=all(d['motors'][key] is not None for key in ('target_cm_s','measured_cm_s','pwm','encoder_count'))
        if self.pending_direct is not None:
            client,deadline=self.pending_direct
            if (client is not self.engine.client or tr.get('session_lost') or tr.get('guard_error') or
                (tr.get('last_rx_trusted') and (tr.get('last_rx_json') or {}).get('fault') not in (None,'NONE')) or
                time.monotonic()>=deadline):
                self.pending_direct=None
                self.engine.notice='직접 제어 활성화 실패. 진단에서 정지 응답과 장치 오류를 확인하세요.'
            elif proof and complete and tr.get('ready'):
                self.pending_direct=None
                self.engine.enable_direct()
        activating=self.pending_direct is not None
        self.manual_arm.setText('바퀴 시험 선택')
        self.manual_arm.setEnabled(bool(shared and not self.engine.language.executing))
        self.heartbeat_button.setEnabled(bool(shared and tr.get('stop_verified') and not self.engine.language.executing))
        ready=proof and bool(tr.get('direct_enabled') and tr.get('manual_enabled'))
        if manual:
            for direction,button in self.test_buttons.items():
                button.setEnabled(bool(ready or direction==self.held_direction and tr.get('direct_enabled') and trial and trial['ended_at'] is None))
        for i in range(3):
            m=d['motors']
            def at(key):return m[key][i] if m[key] is not None else None
            values=[f'M{i+1}',text(at('target_rpm')),text(at('measured_rpm')),
                    text(at('pwm')),text(at('encoder_count'))]
            for j,value in enumerate(values):
                item=QTableWidgetItem(ko(value));item.setToolTip(ko(value));self.bench_motors.setItem(i,j,item)
        processing=('TELEMETRY SESSION / SEQ / STATE / CMD MATCH' if last['command_processed'] else 'NOT OBSERVED') if last else 'NOT TESTED'
        self.bench_state.update_values({'Source':motor['evidence_source'] if motor else 'NO MOTOR TELEMETRY / NOT TESTED',
            'Status / reason':f'{self.engine.client_owner if shared else motor["status"] if motor else "DISABLED"} / {tr["reason"]}',
            'Command processing evidence':processing,
            'Stop feedback':'FRESH STOPPED TELEMETRY VERIFIED' if tr.get('stop_verified') else 'NOT VERIFIED',
            '최근 장치 오류':(tr.get('telemetry') or {}).get('last_fault') or tr.get('fault'),
            'Current / last trial':f'{last["session_id"]}:{last["id"]} {last["direction"]}' if last else 'NOT TESTED'})
        ids=[(t['session_id'],t['id'],t['physical_result']) for t in history]
        if ids!=getattr(self,'_trial_ids',None):
            selected_id=self.trial_selection.currentData()
            self._trial_ids=ids;self.trial_selection.clear()
            for t in history:self.trial_selection.addItem(f'세션 {t["session_id"]} / 시험 {t["id"]}: {ko(t["direction"])} / {ko(t["physical_result"])}',t['id'])
            if history:
                index=next((i for i,t in enumerate(history) if t['id']==selected_id),len(history)-1)
                self.trial_selection.setCurrentIndex(index)
        selected=next((t for t in history if t['id']==self.trial_selection.currentData()),None) or trial
        report=('시험 원문: 명령 처리·엔코더 관측·사용자 육안 관찰은 별개입니다. JSON 필드명은 원문입니다.\n'+json.dumps(selected,ensure_ascii=False,indent=2)) if selected else ko('No motor trial performed. Actual hardware: NOT TESTED.')
        if self.trial_text.toPlainText()!=report:self.trial_text.setPlainText(report)

    def update_connection_diagnostics(self,d):
        host=self.engine.client.host if self.engine.client else self.host.text().strip()
        motor=d.get('motor_test') or {};trial=motor.get('trial') or (motor.get('history') or [None])[-1]
        network=self.network_probe.result
        if network and network['target']!=host:network=None
        attempt=self.engine.connection_attempt
        if attempt and attempt.get('host')!=host:attempt=None
        report=diagnostic_report(host,d['transport'],network,attempt,trial)
        self.last_diagnostic_report=report
        names=['① ESP32 IP 설정','② UDP 수신 대기','③ ESP32 응답','④ 세션·생존 확인','⑤ ESP32 제어 상태','⑥ 모터 시험 준비']
        status={'OK':'정상','FAIL':'실패','PENDING':'확인 중','NOT_TESTED':'미검증'}
        for i,step in enumerate(report['steps']):
            for j,value in enumerate((names[i],status[step['status']],step['message'])):
                self.diagnostic_steps.setItem(i,j,QTableWidgetItem(value))
        self.diagnostic_steps.resizeRowsToContents()
        self.network_check.setEnabled(not self.network_probe.running)
        n=network
        if self.network_probe.running:self.network_status.setText('네트워크와 수신 포트를 무구동 점검 중입니다. 화면 조작과 정지 감시는 계속 동작합니다.')
        elif self.network_probe.error:self.network_status.setText('네트워크 점검 오류: '+self.network_probe.error+' · 현재 환경의 권한과 네트워크를 확인하세요.')
        elif n:
            same={True:'같은 직접 연결 대역',False:'다른 대역 (라우팅 여부 추가 확인 필요)',None:'대역 미확인'}[n['same_subnet']]
            ping={True:'응답 있음',False:'응답 없음 — 이것만으로 UDP 실패를 확정하지 않음',None:'미검증'}[n['ping_reply']]
            active=next((p.get('NetworkCategory') for p in n.get('connection_profiles',[]) if p.get('InterfaceAlias')==n.get('selected_interface')),None)
            category={'Public':'공용','Private':'개인','DomainAuthenticated':'도메인',0:'공용',1:'개인',2:'도메인'}.get(active,'미확인')
            enabled=[{'Domain':'도메인','Private':'개인','Public':'공용'}.get(p.get('Name'),str(p.get('Name'))) for p in n.get('firewall_profiles',[]) if str(p.get('Enabled')).lower() in ('true','1')]
            self.network_status.setText(f'점검 시각 {n["checked_at"]} | 노트북 선택 IP {n["selected_local_ip"]} | 대상 {n["target"]}\n{same} | ICMP {ping} | 수신 포트 {text(n["port_available"])}\n현재 연결 범주: {category} | 방화벽 사용 프로필: {", ".join(enabled) or "미확인"}\n포트 점유 PID와 프로필 원문은 아래에서 확인하세요. 방화벽의 UDP 차단 여부와 공유기·핫스팟 단말 격리는 추가 확인이 필요합니다.')
        else:self.network_status.setText('현재 입력 주소에 대한 네트워크 점검은 미검증입니다. 네트워크 변경 후 다시 점검하세요.')
        t=d['transport']
        details=dict(설명='송신 성공·수신 검증·실제 회전은 별개이며 JSON 필드명은 원문입니다.',
            시각기준='송수신 시각은 실행 내 경과 비교용 단조 시계(초)이며 JSONL 각 행에는 실제 시각이 별도로 기록됩니다.',
            송신=dict(시각=t.get('last_tx_at'),목적지=t.get('tx_destination'),JSON=t.get('last_tx'),소켓오류=t.get('tx_error_details') or t.get('tx_error')),
            수신=dict(시각=t.get('last_rx_any_at'),송신자=t.get('last_rx_source'),JSON=t.get('last_rx_json'),검증사유=ko(t.get('validation_detail','NO_TELEMETRY')),거부집계=t.get('rx_rejections')),
            네트워크원문=network,
            통신감시중단=t.get('guard_error'),
            원인후보=report['findings'])
        value=json.dumps(details,ensure_ascii=False,indent=2)
        if self.communication_text.toPlainText()!=value:self.communication_text.setPlainText(value)

    def apply_style(self):
        self.setStyleSheet('''
          QMainWindow,QWidget { background:#111923; color:#dfE7f0; font-family:Malgun Gothic; font-size:12px; }
          QLabel#title { font-size:17px; font-weight:700; letter-spacing:1px; }
          QLabel#mode { background:#654719; color:#ffda91; padding:8px; border-radius:5px; font-weight:700; }
          QLabel#mode[hardware="true"] { background:#772c35; color:#ffe0e2; }
          QGroupBox { background:#1a2533; border:1px solid #2d3d50; border-radius:6px; margin-top:12px; padding:12px 8px 6px; font-weight:600; }
          QGroupBox::title { subcontrol-origin:margin; left:10px; color:#83b8f9; }
          QGroupBox QLabel { background:transparent; font-weight:400; }
          QPushButton { background:#293e55; border:1px solid #3a5571; padding:9px 12px; border-radius:4px; }
          QPushButton:hover { background:#365573; }
          QPushButton:pressed { background:#406f92; border-color:#95c7ff; }
          QPushButton:disabled { background:#1a2634; border-color:#293849; color:#66788e; }
          QPushButton#estop { background:#ba3e48; border:1px solid #df626c; color:white; font-weight:700; }
          QTabWidget::pane { border:1px solid #2d3d50; }
          QTabBar::tab { padding:10px 22px; background:#172332; }
          QTabBar::tab:selected { color:#9dcaff; background:#26394f; }
          QLineEdit,QPlainTextEdit,QComboBox,QTableWidget { background:#101923; border:1px solid #34465d; padding:6px; }
          QHeaderView::section { background:#26394f; color:#dfe7f0; padding:8px; border:0; }
          QProgressBar { border:1px solid #34465d; text-align:center; height:18px; }
          QProgressBar::chunk { background:#3f86bd; }
        ''')

    def closeEvent(self,event):
        if not self.closing:
            self.closing=True;self.timer.stop();self.engine.request('ESTOP')
            QApplication.instance().removeEventFilter(self)
            try:self.engine.close()
            except Exception as exc:self.shutdown_error=str(exc)
            finally:self.stream.close()
        event.accept()


def run_dashboard(config,hardware=False,preview=False,smoke_seconds=None):
    app=QApplication.instance() or QApplication([])
    window=Dashboard(config,hardware,preview)
    window.show()
    if smoke_seconds is not None:QTimer.singleShot(int(smoke_seconds*1000),window.close)
    previous=signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT,lambda signum,frame:window.close())
    try:result=app.exec()
    finally:
        window.close()
        signal.signal(signal.SIGINT,previous)
    return 1 if getattr(window,'shutdown_error',None) else result
