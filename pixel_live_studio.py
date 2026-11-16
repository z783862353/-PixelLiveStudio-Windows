import os, sys, json, shutil, subprocess, threading, queue, time, re, uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor

APP_NAME = 'Pixel Live Studio'


def app_dir():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent


def find_tool(name):
    candidates = [app_dir() / (name + ('.exe' if os.name == 'nt' else '')),
                  app_dir() / 'ffmpeg' / (name + ('.exe' if os.name == 'nt' else ''))]
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidates.insert(0, Path(sys._MEIPASS) / (name + ('.exe' if os.name == 'nt' else '')))
    for p in candidates:
        if p.exists(): return str(p)
    return shutil.which(name)


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or '外部程序执行失败')[-1800:])
    return p


def inject_xmp_and_video(jpg_path: Path, mp4_path: Path, out_path: Path, presentation_us: int):
    jpg = jpg_path.read_bytes()
    if not jpg.startswith(b"\xff\xd8") or not jpg.endswith(b"\xff\xd9"):
        raise RuntimeError("封面图不是完整有效的 JPEG")
    video = mp4_path.read_bytes()
    if not video:
        raise RuntimeError("动态视频为空")

    # Android Motion Photo Format 1.0: Camera metadata + Container directory.
    # Keep legacy GCamera MicroVideo fields as a compatibility hint for older galleries.
    xmp_text = f'''http://ns.adobe.com/xap/1.0/\x00''' + f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Pixel Live Studio">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 <rdf:Description rdf:about=""
  xmlns:Camera="http://ns.google.com/photos/1.0/camera/"
  xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
  xmlns:Container="http://ns.google.com/photos/1.0/container/"
  xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
  Camera:MotionPhoto="1" Camera:MotionPhotoVersion="1"
  Camera:MotionPhotoPresentationTimestampUs="{presentation_us}"
  GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1"
  GCamera:MotionPhotoPresentationTimestampUs="{presentation_us}"
  GCamera:MicroVideo="1" GCamera:MicroVideoVersion="1"
  GCamera:MicroVideoPresentationTimestampUs="{presentation_us}"
  GCamera:MicroVideoOffset="{len(video)}">
  <Container:Directory>
   <rdf:Seq>
    <rdf:li rdf:parseType="Resource">
     <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" />
    </rdf:li>
    <rdf:li rdf:parseType="Resource">
     <Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{len(video)}" />
    </rdf:li>
   </rdf:Seq>
  </Container:Directory>
 </rdf:Description>
</rdf:RDF>
</x:xmpmeta>'''
    xmp = xmp_text.encode("utf-8")
    if len(xmp) + 2 > 65535:
        raise RuntimeError("XMP 元数据过长")
    app1 = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
    # Validate the embedded MP4 before writing the final Motion Photo container.
    if not video.startswith(b'\x00\x00\x00') and b'ftyp' not in video[:32]:
        raise RuntimeError('动态视频不是有效的 MP4 容器')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(jpg[:2])
        f.write(app1)
        f.write(jpg[2:])
        f.write(video)
    # Basic structural verification: JPEG must end before the appended MP4, and
    # the declared MicroVideoOffset must equal the appended payload length.
    result = out_path.read_bytes()
    eoi = result.find(b'\xff\xd9')
    if eoi < 0 or len(result) - (eoi + 2) != len(video):
        out_path.unlink(missing_ok=True)
        raise RuntimeError('Motion Photo 封装校验失败')


class Converter:
    def __init__(self, ffmpeg, ffprobe, settings, notify):
        self.ffmpeg, self.ffprobe, self.s, self.notify = ffmpeg, ffprobe, settings, notify

    def convert(self, src: Path, dest_dir: Path):
        name = src.stem
        duration = float(self.s['duration'])
        start = float(self.s['start'])
        fps = int(self.s['fps'])
        width = self.s['width']
        quality = int(self.s['quality'])
        temp = dest_dir / '.pls_temp'
        temp.mkdir(parents=True, exist_ok=True)
        token = str(abs(hash((str(src), time.time_ns()))))
        jpg = temp / f'{token}.jpg'; mp4 = temp / f'{token}.mp4'
        # UUID suffix prevents filename collisions when same-named files are
        # converted concurrently from different source folders.
        out = dest_dir / f'{name}_MP_{uuid.uuid4().hex[:6]}.jpg'
        try:
            probe = run([self.ffprobe, '-v','error','-show_entries','format=duration','-of','json',str(src)])
            total = float(json.loads(probe.stdout)['format']['duration'])
            actual_start = max(0.0, min(start, max(0.0, total - 0.05)))
            actual_duration = min(duration, max(0.05, total - actual_start))
            seek = actual_start + actual_duration / 2
            vf = r"scale=trunc(min(iw\,%s)/2)*2:-2" % width if width != '原始' else 'scale=trunc(iw/2)*2:trunc(ih/2)*2'
            run([self.ffmpeg,'-y','-ss',f'{seek:.3f}','-i',str(src),'-frames:v','1','-vf',vf,'-q:v','2',str(jpg)])
            crf = {1:18, 2:23, 3:28}.get(quality, 20)
            run([self.ffmpeg,'-y','-ss',f'{actual_start:.3f}','-i',str(src),'-t',f'{actual_duration:.3f}',
                 '-an','-vf',vf+f',fps={fps}','-c:v','libx264','-preset','medium','-crf',str(crf),
                 '-pix_fmt','yuv420p','-movflags','+faststart','-metadata','comment=Pixel Live Studio Motion Photo',str(mp4)])
            inject_xmp_and_video(jpg, mp4, out, int((actual_duration/2)*1_000_000))
            return out
        finally:
            for p in (jpg, mp4):
                try: p.unlink(missing_ok=True)
                except: pass
            try: temp.rmdir()
            except: pass


class App:
    BG='#f6f5fb'; CARD='#ffffff'; TEXT='#20212a'; MUTED='#6e7080'; ACCENT='#6750a4'; SOFT='#eee8f8'; GREEN='#287a52'; RED='#b3261e'
    def __init__(self, root):
        self.root=root; root.title('Pixel Live Studio'); root.geometry('1060x760'); root.minsize(900,650); root.configure(bg=self.BG)
        self.files=[]; self.dest= tk.StringVar(value=str(Path.home()/'Pictures'/'Pixel Live Studio')); self.start=tk.StringVar(value='0'); self.duration=tk.StringVar(value='3.0'); self.fps=tk.StringVar(value='30'); self.width=tk.StringVar(value='1080'); self.quality=tk.IntVar(value=1); self.parallel=tk.IntVar(value=2)
        self.events=queue.Queue(); self.rows={}; self.running=False
        self.ffmpeg=find_tool('ffmpeg'); self.ffprobe=find_tool('ffprobe')
        self.build(); self.root.after(100,self.poll)

    def build(self):
        top=tk.Frame(self.root,bg=self.BG); top.pack(fill='x',padx=30,pady=(24,14))
        tk.Label(top,text='Pixel Live Studio',font=('Segoe UI',25,'bold'),bg=self.BG,fg=self.TEXT).pack(anchor='w')
        tk.Label(top,text='把 MP4 转换为 Android Motion Photo 动态照片',font=('Segoe UI',11),bg=self.BG,fg=self.MUTED).pack(anchor='w',pady=(3,0))
        body=tk.Frame(self.root,bg=self.BG); body.pack(fill='both',expand=True,padx=30,pady=8)
        left=tk.Frame(body,bg=self.CARD,highlightthickness=1,highlightbackground='#e8e5ee'); left.pack(side='left',fill='both',expand=True,padx=(0,12))
        right=tk.Frame(body,bg=self.CARD,highlightthickness=1,highlightbackground='#e8e5ee',width=300); right.pack(side='right',fill='y'); right.pack_propagate(False)
        hdr=tk.Frame(left,bg=self.CARD); hdr.pack(fill='x',padx=20,pady=18)
        tk.Label(hdr,text='转换队列',font=('Segoe UI',15,'bold'),bg=self.CARD,fg=self.TEXT).pack(side='left')
        self.count=tk.Label(hdr,text='0 个文件',bg=self.CARD,fg=self.MUTED); self.count.pack(side='right')
        bar=tk.Frame(left,bg=self.CARD); bar.pack(fill='x',padx=20,pady=(0,12))
        self.button(bar,'＋ 添加视频',self.add_files,True).pack(side='left')
        self.button(bar,'移除全部',self.clear_files).pack(side='left',padx=8)
        self.listbox=tk.Listbox(left,bg='#fbfaff',fg=self.TEXT,selectbackground=self.SOFT,selectforeground=self.TEXT,relief='flat',borderwidth=0,font=('Segoe UI',10),activestyle='none')
        self.listbox.pack(fill='both',expand=True,padx=20,pady=(0,20))
        # settings
        tk.Label(right,text='转换设置',font=('Segoe UI',15,'bold'),bg=self.CARD,fg=self.TEXT).pack(anchor='w',padx=20,pady=(18,14))
        self.field(right,'起始时间（秒）',self.start)
        self.field(right,'动态时长（秒）',self.duration)
        self.combo(right,'帧率',self.fps,['24','30','60'])
        self.combo(right,'视频宽度',self.width,['原始','1080','720','480'])
        tk.Label(right,text='画质',bg=self.CARD,fg=self.TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=20,pady=(12,4))
        q=tk.Frame(right,bg=self.CARD); q.pack(anchor='w',padx=20)
        for label,val in [('高',1),('均衡',2),('小体积',3)]: tk.Radiobutton(q,text=label,value=val,variable=self.quality,bg=self.CARD,fg=self.TEXT,activebackground=self.CARD,selectcolor=self.SOFT).pack(side='left')
        self.combo(right,'并行任务数',self.parallel,['1','2','3','4'])
        tk.Label(right,text='输出文件夹',bg=self.CARD,fg=self.TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=20,pady=(14,5))
        row=tk.Frame(right,bg=self.CARD); row.pack(fill='x',padx=20)
        tk.Entry(row,textvariable=self.dest,relief='flat',bg='#f4f1f8',font=('Segoe UI',9)).pack(side='left',fill='x',expand=True,ipady=7)
        tk.Button(row,text='…',command=self.choose_dest,bg=self.SOFT,fg=self.ACCENT,relief='flat').pack(side='right',padx=(5,0),ipady=3)
        self.start_btn=tk.Button(right,text='开始批量转换',command=self.start_convert,bg=self.ACCENT,fg='white',activebackground='#57418d',activeforeground='white',relief='flat',font=('Segoe UI',11,'bold'),cursor='hand2'); self.start_btn.pack(fill='x',padx=20,pady=(20,10),ipady=11)
        self.status=tk.Label(right,text='就绪 · 添加 MP4 开始转换',bg=self.CARD,fg=self.MUTED,wraplength=250,justify='left'); self.status.pack(anchor='w',padx=20,pady=4)
        foot=tk.Label(self.root,text='输出：JPG + 内嵌 MP4（Android Motion Photo XMP）  ·  抖音识别效果需在目标手机实测',bg=self.BG,fg=self.MUTED,font=('Segoe UI',9)); foot.pack(anchor='w',padx=32,pady=(0,14))

    def button(self,parent,text,cmd,primary=False): return tk.Button(parent,text=text,command=cmd,bg=self.SOFT if primary else '#f4f1f8',fg=self.ACCENT if primary else self.TEXT,relief='flat',font=('Segoe UI',9,'bold' if primary else 'normal'),cursor='hand2',padx=14,pady=8)
    def field(self,parent,label,var):
        tk.Label(parent,text=label,bg=self.CARD,fg=self.TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=20,pady=(8,4))
        tk.Entry(parent,textvariable=var,bg='#f4f1f8',fg=self.TEXT,relief='flat',font=('Segoe UI',10)).pack(fill='x',padx=20,ipady=7)
    def combo(self,parent,label,var,values):
        tk.Label(parent,text=label,bg=self.CARD,fg=self.TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=20,pady=(10,4))
        ttk.Combobox(parent,textvariable=var,values=values,state='readonly').pack(fill='x',padx=20,ipady=3)
    def add_files(self):
        paths=filedialog.askopenfilenames(title='选择视频',filetypes=[('视频文件','*.mp4 *.mov *.m4v *.mkv'),('所有文件','*.*')])
        for p in paths:
            pp=Path(p)
            if pp not in self.files: self.files.append(pp); self.listbox.insert('end',f'●  {pp.name}    ·    等待转换')
        self.count.config(text=f'{len(self.files)} 个文件')
    def clear_files(self):
        if self.running: return
        self.files=[]; self.listbox.delete(0,'end'); self.count.config(text='0 个文件')
    def choose_dest(self):
        p=filedialog.askdirectory(initialdir=self.dest.get() or str(Path.home()))
        if p: self.dest.set(p)
    def start_convert(self):
        if self.running: return
        if not self.files: messagebox.showinfo('提示','请先添加 MP4 视频。'); return
        if not self.ffmpeg or not self.ffprobe:
            messagebox.showerror('缺少 FFmpeg','未找到 ffmpeg 和 ffprobe。请安装 FFmpeg 并将其加入 PATH，或把 ffmpeg.exe、ffprobe.exe 放在程序同目录。'); return
        try:
            s=float(self.start.get()); d=float(self.duration.get())
            if s<0 or d<=0 or d>30: raise ValueError()
        except: messagebox.showerror('参数错误','起始时间必须 ≥ 0，动态时长须在 0 到 30 秒之间。'); return
        dest=Path(self.dest.get()); dest.mkdir(parents=True,exist_ok=True)
        settings={'start':s,'duration':d,'fps':self.fps.get(),'width':self.width.get(),'quality':self.quality.get(),'parallel':int(self.parallel.get())}
        self.running=True; self.start_btn.config(state='disabled'); self.status.config(text='正在转换…')
        for i,p in enumerate(self.files): self.listbox.delete(i); self.listbox.insert(i,f'◷  {p.name}    ·    排队中')
        threading.Thread(target=self.worker,args=(settings,dest),daemon=True).start()
    def worker(self,settings,dest):
        maxw=int(settings.pop('parallel')); conv=Converter(self.ffmpeg,self.ffprobe,settings,None)
        with ThreadPoolExecutor(max_workers=maxw) as ex:
            futs={ex.submit(conv.convert,p,dest):(i,p) for i,p in enumerate(self.files)}
            for f in futs:
                i,p=futs[f]
                try:
                    out=f.result(); self.events.put(('ok',i,p.name,str(out)))
                except Exception as e: self.events.put(('err',i,p.name,str(e)))
        self.events.put(('done',))
    def poll(self):
        try:
            while True:
                e=self.events.get_nowait()
                if e[0]=='ok':
                    _,i,n,out=e; self.listbox.delete(i); self.listbox.insert(i,f'✓  {n}    ·    已完成 → {Path(out).name}')
                elif e[0]=='err':
                    _,i,n,err=e; self.listbox.delete(i); self.listbox.insert(i,f'!  {n}    ·    失败：{err[:90]}')
                elif e[0]=='done':
                    self.running=False; self.start_btn.config(state='normal'); self.status.config(text='批量任务已结束。请在三星相册检查动态效果，并测试抖音上传识别。'); messagebox.showinfo('转换完成','批量转换任务已结束。\n请在目标安卓手机上验证动态照片识别和抖音上传效果。')
        except queue.Empty: pass
        self.root.after(120,self.poll)

if __name__=='__main__':
    root=tk.Tk(); app=App(root); root.mainloop()
