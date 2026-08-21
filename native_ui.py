# ============================================================
# CryptoEdge – przyjazny interfejs natywny (Tkinter)
# ============================================================

import json
from datetime import datetime
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Optional

from runtime import BotRuntime
import config
import settings_store
import secrets_store
from tradingview_link import open_chart, chart_url

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "logs" / "bot_state.json"

# Paleta
BG = "#080d16"
CARD = "#0f1724"
CARD2 = "#151f2f"
BORDER = "#26354a"
TEXT = "#edf4ff"
MUTED = "#8797b0"
BLUE = "#42c5f5"
GREEN = "#35d07f"
RED = "#ff5c67"
AMBER = "#f6b84b"
PURPLE = "#a78bfa"
CYAN = "#67e8f9"


def money(v, d=4):
    try:
        return f"${float(v):,.{d}f}"
    except Exception:
        return "—"


def pct(v, d=2):
    try:
        n = float(v)
        return f"{n:+.{d}f}%"
    except Exception:
        return "—"


class CryptoEdgeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CryptoEdge")
        self.geometry("1440x900")
        self.minsize(1180, 720)
        self.configure(bg=BG)
        self.rt = BotRuntime.get()
        self._stop_poll = False
        self._refresh_every = int(getattr(config, "LOOP_INTERVAL_SECONDS", 1) or 1)
        self._countdown = self._refresh_every
        self._equity_history = []
        self._closed_rows = []
        self._health_mode = "overview"
        self._layout_vars = {}
        self._pulse_on = False
        self._seen_closed_ids = set()
        self._last_alert_text = ""

        self._style()
        self._ui()
        self.after(400, self._poll)
        self.after(200, self._tick_clock)
        self.bind_all("<F5>", lambda e: self._force_refresh())
        self.bind_all("<Control-Key-1>", lambda e: self._select_tab(0))
        self.bind_all("<Control-Key-2>", lambda e: self._select_tab(1))
        self.bind_all("<Control-Key-3>", lambda e: self._select_tab(2))
        self.bind_all("<Control-Key-4>", lambda e: self._select_tab(3))
        self.bind_all("<Control-Key-5>", lambda e: self._select_tab(4))
        self.bind_all("<Control-Key-6>", lambda e: self._select_tab(5))
        self.bind_all("<KeyPress-p>", lambda e: self._toggle_pause())
        self.bind_all("<Escape>", lambda e: self._on_escape())
        self.after(650, self._pulse_status)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- style ----------
    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=BG, foreground=BLUE, font=("Segoe UI", 16, "bold"))
        style.configure("StatusLive.TLabel", background=BG, foreground=GREEN, font=("Segoe UI", 9, "bold"))
        style.configure("StatusPause.TLabel", background=BG, foreground=AMBER, font=("Segoe UI", 9, "bold"))
        style.configure("StatusHalt.TLabel", background=BG, foreground=RED, font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD2, foreground=MUTED, padding=[14, 8], font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", BLUE)])
        style.configure("Treeview",
                        background=CARD, foreground=TEXT, fieldbackground=CARD,
                        borderwidth=0, rowheight=30, font=("Segoe UI", 9))
        style.configure("Treeview.Heading",
                        background=CARD2, foreground=MUTED,
                        font=("Segoe UI", 8, "bold"), relief="flat",
                        padding=[7, 7])
        style.map("Treeview", background=[("selected", "#1e3a5f")], foreground=[("selected", TEXT)])
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=8)
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"))

    # ---------- helpers ----------
    def _card(self, parent, **kw):
        f = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, **kw)
        return f

    def _kpi(self, parent, title: str):
        box = self._card(parent)
        box.pack(side="left", expand=True, fill="both", padx=5, pady=2)
        tk.Label(box, text=title.upper(), bg=CARD, fg=MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        var = tk.StringVar(value="—")
        accent = tk.Frame(box, bg=BLUE, height=2)
        accent.pack(fill="x", side="bottom")
        lbl = tk.Label(box, textvariable=var, bg=CARD, fg=TEXT,
                       font=("Segoe UI", 17, "bold"))
        lbl.pack(anchor="w", padx=12, pady=(2, 3))
        sub = tk.StringVar(value="")
        tk.Label(box, textvariable=sub, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 9))
        return var, sub, lbl

    def _tree(self, parent, cols):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=2, pady=2)
        names = [c[0] for c in cols]
        tree = ttk.Treeview(wrap, columns=names, show="headings", selectmode="browse")
        for cid, title, w in cols:
            tree.heading(cid, text=title)
            tree.column(cid, width=w, minwidth=50, anchor="center")
        sy = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sy.set)
        tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        tree.tag_configure("pos", foreground=GREEN, background="#10231d")
        tree.tag_configure("neg", foreground=RED, background="#28171c")
        tree.tag_configure("neutral", foreground=MUTED, background=CARD)
        tree.tag_configure("long", foreground=GREEN, background="#10231d")
        tree.tag_configure("short", foreground=RED, background="#28171c")
        # SL już w zysku (trailing powyżej/poniżej entry)
        tree.tag_configure("sl_profit", foreground=BLUE)
        return tree

    # ---------- UI ----------
    def _ui(self):
        """Native Dark Modern terminal.  No browser/webview; preserves all runtime controls and data widgets."""
        # ---- top command bar ----
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True)

        top = tk.Frame(root, bg=BG, height=68)
        top.pack(fill="x", padx=18, pady=(12, 6))
        top.pack_propagate(False)

        brand = tk.Frame(top, bg=BG)
        brand.pack(side="left", fill="y")
        tk.Label(brand, text="CE", bg=BLUE, fg=BG, font=("Segoe UI", 11, "bold"), padx=8, pady=5).pack(side="left", padx=(0,10), pady=8)
        tk.Label(brand, text="CryptoEdge", bg=BG, fg=TEXT, font=("Segoe UI", 17, "bold")).pack(side="left", pady=7)
        tk.Label(brand, text="  TRADING CONSOLE", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", pady=9)

        controls = tk.Frame(top, bg=BG)
        controls.pack(side="right", fill="y")
        self.status_lbl = tk.Label(controls, text="● STARTING", bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self.status_lbl.pack(side="left", padx=(0,10), pady=15)
        self.clock_var=tk.StringVar(value="--:--:--"); self.countdown_var=tk.StringVar(value=f"REFRESH {self._refresh_every}s")
        self.uptime_var=tk.StringVar(value="⏱ 00:00:00")
        tk.Label(controls,textvariable=self.clock_var,bg=BG,fg=BLUE,font=("Segoe UI",10,"bold")).pack(side="left",padx=5)
        tk.Label(controls,textvariable=self.countdown_var,bg=BG,fg=MUTED,font=("Segoe UI",8)).pack(side="left",padx=5)
        tk.Label(controls,textvariable=self.uptime_var,bg=BG,fg=GREEN,font=("Segoe UI",9,"bold")).pack(side="left",padx=(5,12))

        def btn(parent,text,cmd,bg=CARD2,fg=TEXT,w=None):
            kw=dict(text=text,command=cmd,bg=bg,fg=fg,activebackground=BORDER,activeforeground=TEXT,relief="flat",bd=0,padx=10,pady=6,font=("Segoe UI",8,"bold"),cursor="hand2")
            if w: kw["width"]=w
            return tk.Button(parent,**kw)
        mode= tk.Frame(controls,bg=BG); mode.pack(side="left",padx=5)
        self.btn_demo=btn(mode,"DEMO",lambda:self._set_mode(True),"#123d2b","#8ff0ba"); self.btn_demo.pack(side="left",padx=2)
        self.btn_live=btn(mode,"LIVE",lambda:self._set_mode(False),"#2a1820","#ff9aa4"); self.btn_live.pack(side="left",padx=2)
        self.btn_analysis=btn(controls,"▶ ANALIZA",self._start_analysis,"#17365b","#9bcfff"); self.btn_analysis.pack(side="left",padx=2)
        self.btn_trade=btn(controls,"▶ HANDEL",self._start_trading,"#123d2b","#8ff0ba"); self.btn_trade.pack(side="left",padx=2)
        self.btn_start=self.btn_trade
        self.btn_stop=btn(controls,"■ STOP",self._stop_engine,"#321b22","#ff9aa4"); self.btn_stop.pack(side="left",padx=2)
        self.btn_pause=btn(controls,"Ⅱ PAUSE",self._pause,"#2a2418","#f6d58b"); self.btn_pause.pack(side="left",padx=2)
        self.btn_resume=btn(controls,"▶ RESUME",self._resume,"#16352b","#8ff0ba"); self.btn_resume.pack(side="left",padx=2)
        self.btn_close=btn(controls,"CLOSE ALL",self._close_all,"#4a2028","#ffb1b8"); self.btn_close.pack(side="left",padx=2)
        self.btn_settings=btn(controls,"⚙",self._open_settings_window,CARD2,BLUE); self.btn_settings.pack(side="left",padx=2)
        self.btn_layout=btn(controls,"▦",self._open_layout_window,CARD2,PURPLE); self.btn_layout.pack(side="left",padx=2)
        self.btn_tv=btn(controls,"TV",lambda:self._open_tv(interval="240"),CARD2,BLUE); self.btn_tv.pack(side="left",padx=2)

        # ---- operational strip ----
        ops=tk.Frame(root,bg=BG); ops.pack(fill="x",padx=18,pady=(0,8))
        self.ops_market_var=tk.StringVar(value="MARKETS —")
        self.ops_regime_var=tk.StringVar(value="REGIME —")
        self.ops_feed_var=tk.StringVar(value="DATA —")
        self.ops_risk_var=tk.StringVar(value="RISK —")
        for var,color in [(self.ops_market_var,BLUE),(self.ops_regime_var,PURPLE),(self.ops_feed_var,MUTED),(self.ops_risk_var,GREEN)]:
            c=self._card(ops); c.pack(side="left",fill="x",expand=True,padx=3)
            tk.Label(c,textvariable=var,bg=CARD,fg=color,font=("Segoe UI",8,"bold"),anchor="w").pack(fill="x",padx=11,pady=7)

        # ---- main shell ----
        shell=tk.Frame(root,bg=BG); shell.pack(fill="both",expand=True,padx=18,pady=(0,8))
        sidebar=tk.Frame(shell,bg="#0a111c",width=170,highlightbackground=BORDER,highlightthickness=1); sidebar.pack(side="left",fill="y",padx=(0,8)); sidebar.pack_propagate(False)
        tk.Label(sidebar,text="WORKSPACE",bg="#0a111c",fg=MUTED,font=("Segoe UI",8,"bold")).pack(anchor="w",padx=14,pady=(16,8))

        content=tk.Frame(shell,bg=BG); content.pack(side="left",fill="both",expand=True)
        nb=ttk.Notebook(content); nb.pack(fill="both",expand=True)
        self.nb=nb
        frames=[tk.Frame(nb,bg=CARD) for _ in range(6)]
        pos_f,exch_f,sig_f,ana_f,hist_f,health_f=frames
        self._tab_frames=frames
        labels=["Overview","Live Account","Signals","Analysis","History","Health"]
        for f,l in zip(frames,labels): nb.add(f,text=l)
        # Hide notebook tabs; sidebar controls the workspace.
        style=ttk.Style(self)
        style.configure("Hidden.TNotebook",background=BG,borderwidth=0,tabmargins=0)
        style.layout("Hidden.TNotebook.Tab", [])
        nb.configure(style="Hidden.TNotebook")
        nav=[]
        for i,(text,icon) in enumerate([( "OVERVIEW","⌂"),("POSITIONS / LIVE","◈"),("SIGNALS","⚡"),("ANALYSIS","◉"),("HISTORY","▤"),("HEALTH","♥")]):
            b=tk.Button(sidebar,text=f"  {icon}  {text}",anchor="w",command=lambda i=i:self._select_tab(i),bg="#0a111c",fg=MUTED,activebackground=CARD2,activeforeground=TEXT,relief="flat",bd=0,font=("Segoe UI",8,"bold"),padx=8,pady=10,cursor="hand2")
            b.pack(fill="x",padx=8,pady=2); nav.append(b)
        self._nav_buttons=nav
        tk.Label(sidebar,text="SYSTEM",bg="#0a111c",fg=MUTED,font=("Segoe UI",8,"bold")).pack(anchor="w",padx=14,pady=(18,6))
        self.sidebar_mode=tk.StringVar(value="DEMO")
        tk.Label(sidebar,textvariable=self.sidebar_mode,bg="#10231d",fg=GREEN,font=("Segoe UI",10,"bold"),anchor="w",padx=10,pady=7).pack(fill="x",padx=8)
        tk.Label(sidebar,text="Hotkeys",bg="#0a111c",fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=14,pady=(14,4))
        tk.Label(sidebar,text="F5  refresh\nP  pause / resume\nCtrl+1…6  workspace\nEsc  close modal",bg="#0a111c",fg="#6f819b",justify="left",font=("Consolas",7),anchor="w").pack(fill="x",padx=14)

        # ---- shared dashboard data ----
        kpi_row=tk.Frame(pos_f,bg=CARD); kpi_row.pack(fill="x",padx=10,pady=(10,8))
        self.kpi={}; self.kpi_boxes={}
        for key,title in [("capital","EQUITY"),("free","FREE MARGIN"),("used","USED MARGIN"),("daily","DAILY PnL"),("open","POSITIONS"),("dd","DRAWDOWN")]:
            var,sub,lbl=self._kpi(kpi_row,title); self.kpi[key]=(var,sub,lbl); self.kpi_boxes[key]=lbl.master
        # second row: positions + opportunities
        body=tk.Frame(pos_f,bg=CARD); body.pack(fill="both",expand=True,padx=10,pady=(0,8))
        left=tk.Frame(body,bg=CARD); left.pack(side="left",fill="both",expand=True,padx=(0,5))
        right=tk.Frame(body,bg=CARD); right.pack(side="right",fill="both",expand=True,padx=(5,0))
        tk.Label(left,text="OPEN POSITIONS",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold"),anchor="w").pack(fill="x",padx=4,pady=(3,5))
        self.pos_tree=self._tree(left,[
            ("dir","SIDE",65),("sym","ASSET",85),("val","VALUE",85),("age","AGE",65),("entry","ENTRY",90),("mkt","MARKET",90),("pnl","PnL %",75),("pnl$","PnL $",85),("mr","MARGIN",70),("sl","SL",85),("act","ACTION",80)])
        self.pos_tree.bind("<Button-1>",self._on_pos_click); self.pos_tree.bind("<Button-3>",self._on_pos_right_click)
        pbar=tk.Frame(left,bg=CARD); pbar.pack(fill="x",pady=6)
        tk.Button(pbar,text="CLOSE ALL POSITIONS",command=self._close_all,bg="#4a2028",fg="#ffb1b8",relief="flat",bd=0,font=("Segoe UI",8,"bold"),padx=10,pady=6).pack(side="left")
        tk.Label(pbar,text="Right click position → close",bg=CARD,fg=MUTED,font=("Segoe UI",7)).pack(side="left",padx=10)
        tk.Label(right,text="TOP OPPORTUNITIES",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold"),anchor="w").pack(fill="x",padx=4,pady=(3,5))
        self.overview_sig_tree=self._tree(right,[("dir","SIDE",65),("sym","ASSET",85),("str","STRENGTH",75),("ch","24h",70),("tr","TREND",80),("path","DECISION",105),("rr","R:R",55),("st","STRATEGY",75),("sec","MTF",180),("why","WHY",250)])
        # market summary / alert bar
        self.market_var=tk.StringVar(value="MARKET CONTEXT —")
        self.alert_var=tk.StringVar(value="")
        bar=tk.Frame(pos_f,bg=CARD2,highlightbackground=BORDER,highlightthickness=1); bar.pack(fill="x",padx=10,pady=(0,8))
        tk.Label(bar,textvariable=self.market_var,bg=CARD2,fg=MUTED,font=("Segoe UI",8),anchor="w").pack(side="left",fill="x",expand=True,padx=10,pady=7)
        self.alert_lbl=tk.Label(bar,textvariable=self.alert_var,bg=CARD2,fg=AMBER,font=("Segoe UI",8,"bold")); self.alert_lbl.pack(side="right",padx=10)

        # ---- LIVE ACCOUNT ----
        self.exch_info=tk.StringVar(value="DEMO / LIVE account snapshot")
        tk.Label(exch_f,textvariable=self.exch_info,bg=CARD,fg=TEXT,font=("Segoe UI",9,"bold"),anchor="w").pack(fill="x",padx=10,pady=(10,6))
        self.exch_tree=self._tree(exch_f,[("dir","SIDE",65),("sym","ASSET",85),("size","SIZE",75),("entry","ENTRY",90),("mark","MARK",90),("pnl","uPnL",90),("lev","LEV",55),("mgn","MARGIN",80),("liq","LIQ",95)])

        # ---- SIGNALS ----
        tk.Label(sig_f,text="SIGNAL MATRIX · RANKED OPPORTUNITIES",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold"),anchor="w").pack(fill="x",padx=10,pady=(10,6))
        self.sig_tree=self._tree(sig_f,[("dir","SIDE",65),("sym","ASSET",85),("str","STRENGTH",75),("ch","24h",70),("tr","TREND",80),("path","DECISION",105),("rr","R:R",55),("st","STRATEGY",75),("sec","MTF",180),("why","WHY",250)])
        self.sig_tree2=self.sig_tree
        # ---- ANALYSIS ----
        ana_body=tk.Frame(ana_f,bg=CARD); ana_body.pack(fill="both",expand=True,padx=8,pady=8)
        left=tk.Frame(ana_body,bg=CARD,width=230); left.pack(side="left",fill="y",padx=(0,8)); left.pack_propagate(False)
        right=tk.Frame(ana_body,bg=CARD); right.pack(side="left",fill="both",expand=True)
        search=tk.Frame(left,bg=CARD); search.pack(fill="x")
        tk.Label(search,text="ASSET SCANNER",bg=CARD,fg=MUTED,font=("Segoe UI",8,"bold")).pack(anchor="w")
        self._ana_search_var=tk.StringVar(); self._ana_search_var.trace_add("write",lambda *_:self._filter_ana_list())
        tk.Entry(search,textvariable=self._ana_search_var,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Segoe UI",9)).pack(fill="x",pady=5,ipady=5)
        self.ana_count_var=tk.StringVar(value="0 assets")
        tk.Label(left,textvariable=self.ana_count_var,bg=CARD,fg=MUTED,font=("Segoe UI",7),anchor="w").pack(fill="x",pady=(0,5))
        lw=tk.Frame(left,bg=CARD); lw.pack(fill="both",expand=True)
        self.ana_list=tk.Listbox(lw,bg=CARD2,fg=TEXT,selectbackground="#163b60",selectforeground=TEXT,font=("Consolas",8),relief="flat",highlightbackground=BORDER,highlightthickness=1,exportselection=False,activestyle="none")
        sy=ttk.Scrollbar(lw,orient="vertical",command=self.ana_list.yview); self.ana_list.configure(yscrollcommand=sy.set); self.ana_list.pack(side="left",fill="both",expand=True); sy.pack(side="right",fill="y")
        self.ana_list.bind("<<ListboxSelect>>",self._on_ana_select); self.ana_list.bind("<Double-Button-1>",lambda e:self._open_tv(interval="240"))
        tvbar=tk.Frame(left,bg=CARD); tvbar.pack(fill="x",pady=5)
        for text,interval,accent in [("15M","15",False),("1H","60",False),("4H","240",True),("1D","D",False)]:
            tk.Button(tvbar,text=text,bg=BLUE if accent else CARD2,fg=BG if accent else TEXT,relief="flat",bd=0,font=("Segoe UI",7,"bold"),command=lambda interval=interval:self._open_tv(interval=interval)).pack(side="left",fill="x",expand=True,padx=1)
        self._ana_cards=[]; self._ana_cards_view=[]; self._ana_selected_sym=None
        # Decision workspace
        self.ana_visual=tk.Frame(right,bg=CARD); self.ana_visual.pack(fill="x",pady=(0,6))
        self.ana_score_var=tk.StringVar(value="—"); self.ana_decision_var=tk.StringVar(value="WAITING"); self.ana_path_var=tk.StringVar(value="—")
        for title,var,color in [("OPPORTUNITY SCORE",self.ana_score_var,BLUE),("DECISION",self.ana_decision_var,GREEN),("DECISION PATH",self.ana_path_var,PURPLE)]:
            c=self._card(self.ana_visual); c.pack(side="left",fill="both",expand=True,padx=3); tk.Label(c,text=title,bg=CARD,fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=10,pady=(8,0)); tk.Label(c,textvariable=var,bg=CARD,fg=color,font=("Segoe UI",14,"bold"),wraplength=250).pack(anchor="w",padx=10,pady=(2,8))
        self.ana_component_frame=self._card(right); self.ana_component_frame.pack(fill="x",pady=(0,6)); tk.Label(self.ana_component_frame,text="CONFLUENCE",bg=CARD,fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=10,pady=(7,2))
        self.ana_component_labels={}; cr=tk.Frame(self.ana_component_frame,bg=CARD); cr.pack(fill="x",padx=8,pady=(0,7))
        for name in ["Trend","MTF","Momentum","Volume","Fibonacci","Divergence","Liquidity"]:
            v=tk.StringVar(value="—"); lab=tk.Label(cr,textvariable=v,bg=CARD2,fg=MUTED,font=("Segoe UI",7,"bold"),padx=4,pady=4); lab.pack(side="left",fill="x",expand=True,padx=2); self.ana_component_labels[name]=(v,lab)
        plan=self._card(right); plan.pack(fill="x",pady=(0,6)); tk.Label(plan,text="PRICE PLAN · ENTRY / SL / TP + FIBONACCI",bg=CARD,fg=TEXT,font=("Segoe UI",8,"bold")).pack(anchor="w",padx=10,pady=(7,2)); self.ana_plan_canvas=tk.Canvas(plan,bg=CARD,highlightthickness=0,height=105); self.ana_plan_canvas.pack(fill="x",padx=8,pady=(0,6))
        dw=tk.Frame(right,bg=CARD2,highlightbackground=BORDER,highlightthickness=1); dw.pack(fill="both",expand=True)
        self.ana_detail=tk.Text(dw,wrap="word",bg=CARD2,fg=TEXT,font=("Consolas",8),relief="flat",padx=10,pady=8,insertbackground=TEXT,state="disabled"); sy=ttk.Scrollbar(dw,orient="vertical",command=self.ana_detail.yview); self.ana_detail.configure(yscrollcommand=sy.set); self.ana_detail.pack(side="left",fill="both",expand=True); sy.pack(side="right",fill="y")
        for tag,color,font in [("title",BLUE,("Segoe UI",11,"bold")),("pro",GREEN,("Segoe UI",9)),("con",RED,("Segoe UI",9)),("muted",MUTED,("Segoe UI",9)),("dec_ok",GREEN,("Segoe UI",10,"bold")),("dec_no",RED,("Segoe UI",10,"bold")),("dec_mid",AMBER,("Segoe UI",10,"bold"))]: self.ana_detail.tag_configure(tag,foreground=color,font=font)

        # ---- HISTORY ----
        split=tk.Frame(hist_f,bg=CARD); split.pack(fill="both",expand=True,padx=8,pady=8)
        hl=tk.Frame(split,bg=CARD); hl.pack(side="left",fill="both",expand=True,padx=(0,6)); hr=tk.Frame(split,bg=CARD,width=270); hr.pack(side="right",fill="y"); hr.pack_propagate(False)
        tk.Label(hl,text="CLOSED TRADES",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold"),anchor="w").pack(fill="x",pady=(2,5))
        self.closed_tree=self._tree(hl,[("t","TIME",75),("dir","SIDE",70),("sym","ASSET",80),("entry","ENTRY",90),("exit","EXIT",90),("pnl","PnL %",75),("pnl$","PnL $",85)])
        self.closed_tree.bind("<Double-Button-1>",self._open_replay_from_history)
        tk.Label(hr,text="PERFORMANCE",bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold"),anchor="w").pack(fill="x",pady=(2,5))
        self.met_vars={}
        for key,title in [("trades","Trades"),("wr","Win rate"),("pf","Profit factor"),("net","Net PnL"),("avg_w","Avg win"),("avg_l","Avg loss"),("exp","Expectancy"),("long","LONG PnL"),("short","SHORT PnL")]:
            c=self._card(hr); c.pack(fill="x",pady=3); tk.Label(c,text=title.upper(),bg=CARD,fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=9,pady=(7,0)); v=tk.StringVar(value="—"); tk.Label(c,textvariable=v,bg=CARD,fg=TEXT,font=("Segoe UI",13,"bold")).pack(anchor="w",padx=9,pady=(2,7)); self.met_vars[key]=v
        self.side_perf_var=tk.StringVar(value="LONG — | SHORT — | TOTAL —"); tk.Label(hr,text="BY SIDE",bg=CARD,fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=4,pady=(8,2)); tk.Label(hr,textvariable=self.side_perf_var,bg=CARD,fg=TEXT,font=("Segoe UI",8,"bold"),wraplength=245,justify="left").pack(fill="x",padx=4)

        # ---- HEALTH ----
        ht=tk.Frame(health_f,bg=CARD); ht.pack(fill="x",padx=8,pady=8)
        self.health_score_var=tk.StringVar(value="—"); self.health_state_var=tk.StringVar(value="WAITING"); self.health_detail_var=tk.StringVar(value="PF · WR · EXPECTANCY · DD")
        for title,var,color in [("STRATEGY HEALTH",self.health_score_var,BLUE),("STATE",self.health_state_var,GREEN),("QUALITY",self.health_detail_var,MUTED)]:
            c=self._card(ht); c.pack(side="left",fill="both",expand=True,padx=3); tk.Label(c,text=title,bg=CARD,fg=MUTED,font=("Segoe UI",7,"bold")).pack(anchor="w",padx=10,pady=(8,0)); tk.Label(c,textvariable=var,bg=CARD,fg=color,font=("Segoe UI",15 if title!="QUALITY" else 9,"bold"),wraplength=260).pack(anchor="w",padx=10,pady=(2,8))
        hm=tk.Frame(health_f,bg=CARD); hm.pack(fill="both",expand=True,padx=8,pady=(0,8))
        eq=self._card(hm); eq.pack(side="left",fill="both",expand=True,padx=(0,4)); tk.Label(eq,text="EQUITY / DRAWDOWN",bg=CARD,fg=TEXT,font=("Segoe UI",9,"bold")).pack(anchor="w",padx=10,pady=(8,2)); self.eq_canvas=tk.Canvas(eq,bg=CARD,highlightthickness=0,height=240); self.eq_canvas.pack(fill="both",expand=True,padx=8,pady=6)
        rp=self._card(hm); rp.pack(side="right",fill="both",expand=True,padx=(4,0)); tk.Label(rp,text="TRADE REPLAY",bg=CARD,fg=TEXT,font=("Segoe UI",9,"bold")).pack(anchor="w",padx=10,pady=(8,2)); self.replay_tree=self._tree(rp,[("time","TIME",75),("sym","ASSET",80),("dir","SIDE",60),("pnl","PnL",80),("rr","R",55)]); self.replay_tree.bind("<<TreeviewSelect>>",self._on_replay_select); self.replay_detail=tk.StringVar(value="Select a closed trade."); tk.Label(rp,textvariable=self.replay_detail,bg=CARD,fg=MUTED,font=("Consolas",8),justify="left",anchor="nw",wraplength=430).pack(fill="x",padx=10,pady=6)
        self.health_alerts=tk.StringVar(value="No active quality alerts."); tk.Label(health_f,textvariable=self.health_alerts,bg=CARD,fg=MUTED,font=("Segoe UI",8),anchor="w").pack(fill="x",padx=10,pady=(0,8))

        # footer
        foot=tk.Frame(root,bg=BG); foot.pack(fill="x",padx=18,pady=(0,8)); self.footer_var=tk.StringVar(value="CryptoEdge · native desktop console · no browser · closing window stops bot"); tk.Label(foot,textvariable=self.footer_var,bg=BG,fg=MUTED,font=("Segoe UI",7)).pack(side="left"); self.cycle_var=tk.StringVar(value=""); tk.Label(foot,textvariable=self.cycle_var,bg=BG,fg=MUTED,font=("Segoe UI",7)).pack(side="right")
        self._select_tab(0)

    # ---------- actions ----------

    def _refresh_mode_buttons(self):
        paper = bool(getattr(config, "PAPER_TRADING", True))
        try:
            if paper:
                self.btn_demo.config(bg="#14532d", fg="#bbf7d0")
                self.btn_live.config(bg=CARD2, fg=MUTED)
            else:
                self.btn_demo.config(bg=CARD2, fg=MUTED)
                self.btn_live.config(bg="#3a1d24", fg="#fecaca")
        except Exception:
            pass

    def _set_mode(self, paper: bool):
        """paper=True → DEMO ($100 paper), False → LIVE (saldo Blofin, tylko odczyt)."""
        try:
            cur = bool(getattr(config, "PAPER_TRADING", True))
            if cur == paper:
                self._refresh_mode_buttons()
                return
            rt = self.rt
            risk = getattr(rt, "risk", None)

            if not paper:
                ok = messagebox.askyesno(
                    "Tryb LIVE",
                    "Włączyć tryb LIVE?\n\n"
                    "• Kapitał = saldo z Blofin (read-only)\n"
                    "• Pozycje z giełdy tylko do podglądu\n"
                    "• Bot NIE otwiera ani nie zamyka zleceń na giełdzie\n"
                    "• Paper pozycje DEMO zostają w pamięci\n\n"
                    "Kontynuować?",
                )
                if not ok:
                    self._refresh_mode_buttons()
                    return
                # przed LIVE zapamiętaj kapitał DEMO
                if risk is not None:
                    try:
                        risk.note_paper_capital()
                    except Exception:
                        pass
            else:
                # powrót do DEMO – przywróć paper capital
                if risk is not None:
                    try:
                        risk.note_paper_capital(getattr(risk, "paper_capital", None) or config.STARTING_CAPITAL)
                        risk.apply_demo_capital()
                    except Exception:
                        try:
                            risk.current_capital = float(config.STARTING_CAPITAL)
                        except Exception:
                            pass

            config.PAPER_TRADING = bool(paper)
            try:
                settings_store.update_setting("PAPER_TRADING", bool(paper))
            except Exception:
                pass

            # odśwież kapitał wg trybu
            sync = getattr(rt, "account_sync", None)
            if paper:
                if risk is not None:
                    cap = risk.apply_demo_capital()
                else:
                    cap = float(config.STARTING_CAPITAL)
                self.alert_var.set(f"Tryb DEMO · kapitał paper ${cap:.2f} · handel lokalny")
                print(f"[UI] Mode → DEMO capital=${cap:.4f}")
            else:
                bal = None
                if sync is not None:
                    try:
                        bal = sync.sync(force=True)
                    except Exception as e:
                        print(f"[UI] LIVE sync: {e}")
                if bal and bal.get("equity") is not None:
                    eq = float(bal.get("equity") or 0)
                    self.alert_var.set(
                        f"Tryb LIVE · Blofin equity ${eq:.4f} · tylko podgląd (brak handlu)"
                    )
                    print(f"[UI] Mode → LIVE equity=${eq:.4f}")
                else:
                    err = (bal or {}).get("error") or (getattr(sync, "_last_error", None) if sync else "brak sync")
                    self.alert_var.set(f"Tryb LIVE · brak salda ({err})")
                    print(f"[UI] Mode → LIVE (no equity): {err}")

            self._refresh_mode_buttons()
            # natychmiast odśwież KPI z state / risk
            try:
                if risk is not None:
                    self.kpi["capital"][0].set(money(risk.current_capital))
                    self.kpi["equity"][0].set(money(risk.current_capital))
            except Exception:
                pass
        except Exception as e:
            self.alert_var.set(f"Mode error: {e}")

    def _open_settings_window(self):
        """Okno ustawień – klucze API Blofin na górze."""
        # zawsze nowe okno (żeby nie trzymać starej wersji bez pól API)
        try:
            if getattr(self, "_settings_win", None) is not None:
                try:
                    self._settings_win.destroy()
                except Exception:
                    pass
        except Exception:
            pass

        win = tk.Toplevel(self)
        win.title("CryptoEdge – Ustawienia")
        win.configure(bg=CARD)
        win.geometry("500x640")
        win.minsize(420, 400)
        win.transient(self)
        self._settings_win = win
        self._settings_vars = {}
        self._secret_vars = {}
        self._secret_entries = []

        tk.Label(win, text="Ustawienia", bg=CARD, fg=BLUE,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Klucze → logs/secrets.bin   ·   reszta → logs/settings.json",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(0, 4))

        # Scrollable body (małe okno / dużo opcji)
        outer = tk.Frame(win, bg=CARD)
        outer.pack(fill="both", expand=True, padx=8, pady=4)
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0)
        sy = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=CARD)
        body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))

        def _settings_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(_e=None):
            canvas.bind_all("<MouseWheel>", _settings_wheel)

        def _unbind_wheel(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        win.bind("<Destroy>", lambda e: _unbind_wheel())
        # szerokość body = canvas
        def _sync_width(event):
            canvas.itemconfigure(canvas.find_all()[0], width=event.width)
        canvas.bind("<Configure>", _sync_width)

        def _section(title):
            tk.Label(body, text=title, bg=CARD, fg=BLUE, font=("Segoe UI", 10, "bold"),
                     anchor="w").pack(fill="x", pady=(12, 4))

        def _check(key, label):
            var = tk.BooleanVar(value=bool(getattr(config, key, settings_store.DEFAULTS.get(key, False))))
            self._settings_vars[key] = var
            tk.Checkbutton(
                body, text=label, variable=var, bg=CARD, fg=TEXT,
                selectcolor=CARD2, activebackground=CARD, activeforeground=TEXT,
                font=("Segoe UI", 9), anchor="w",
                command=lambda k=key, v=var: self._on_setting_toggle(k, v),
            ).pack(fill="x", pady=1)

        def _secret_entry(key, label):
            fr = tk.Frame(body, bg=CARD)
            fr.pack(fill="x", pady=4)
            tk.Label(fr, text=label, bg=CARD, fg=TEXT, font=("Segoe UI", 9),
                     width=14, anchor="w").pack(side="left")
            var = tk.StringVar()
            self._secret_vars[key] = var
            ent = tk.Entry(
                fr, textvariable=var, bg="#0f172a", fg=TEXT, insertbackground=TEXT,
                relief="solid", bd=1, font=("Consolas", 10), show="*",
                highlightthickness=1, highlightbackground=BORDER, highlightcolor=BLUE,
            )
            ent.pack(side="left", fill="x", expand=True, ipady=6, padx=(6, 0))
            self._secret_entries.append(ent)
            return ent

        # ===== API KEYS – zawsze na górze =====
        api_box = tk.Frame(body, bg=CARD2, highlightbackground=BLUE, highlightthickness=1)
        api_box.pack(fill="x", pady=(4, 8), ipady=6, ipadx=6)

        tk.Label(api_box, text="  Klucze API Blofin (LIVE)", bg=CARD2, fg=BLUE,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(
            api_box,
            text="  Wklej key / secret / passphrase z panelu Blofin.\n  Zapis lokalny (obfuskowany) – pod trading LIVE.",
            bg=CARD2, fg=MUTED, font=("Segoe UI", 8), justify="left", anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 6))

        def _secret_entry_in(parent, key, label):
            fr = tk.Frame(parent, bg=CARD2)
            fr.pack(fill="x", pady=3, padx=10)
            tk.Label(fr, text=label, bg=CARD2, fg=TEXT, font=("Segoe UI", 9),
                     width=12, anchor="w").pack(side="left")
            var = tk.StringVar()
            self._secret_vars[key] = var
            ent = tk.Entry(
                fr, textvariable=var, bg="#0b1220", fg=TEXT, insertbackground=TEXT,
                relief="flat", font=("Consolas", 10), show="*",
            )
            ent.pack(side="left", fill="x", expand=True, ipady=7, padx=(4, 0))
            self._secret_entries.append(ent)
            return ent

        _secret_entry_in(api_box, "BLOFIN_API_KEY", "API Key")
        _secret_entry_in(api_box, "BLOFIN_API_SECRET", "API Secret")
        _secret_entry_in(api_box, "BLOFIN_API_PASSPHRASE", "Passphrase")

        self.secrets_status = tk.StringVar(value="Brak zapisanych kluczy")
        tk.Label(api_box, textvariable=self.secrets_status, bg=CARD2, fg=GREEN,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12, pady=(4, 2))

        sec_btns = tk.Frame(api_box, bg=CARD2)
        sec_btns.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(sec_btns, text="Zapisz klucze", bg=BLUE, fg=BG,
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=5,
                  command=self._save_secrets, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(sec_btns, text="Pokaż / Ukryj", bg=BORDER, fg=TEXT,
                  font=("Segoe UI", 8), relief="flat", padx=10, pady=5,
                  command=self._toggle_secret_visibility, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(sec_btns, text="Wyczyść", bg=BORDER, fg=RED,
                  font=("Segoe UI", 8), relief="flat", padx=10, pady=5,
                  command=self._clear_secrets, cursor="hand2").pack(side="left")

        _section("Konto DEMO (paper)")
        demo_fr = tk.Frame(body, bg=CARD)
        demo_fr.pack(fill="x", pady=4)
        tk.Label(demo_fr, text="Startowe saldo $", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._demo_cap_var = tk.StringVar(
            value=str(float(getattr(config, "STARTING_CAPITAL", 100.0)))
        )
        self._settings_vars["STARTING_CAPITAL"] = self._demo_cap_var
        tk.Entry(
            demo_fr, textvariable=self._demo_cap_var, bg=CARD2, fg=TEXT,
            insertbackground=TEXT, relief="flat", font=("Consolas", 11), width=12,
        ).pack(side="left", ipady=5, padx=(4, 8))
        tk.Button(
            demo_fr, text="Zastosuj", bg=GREEN, fg="#0b1220",
            font=("Segoe UI", 8, "bold"), relief="flat", padx=10, pady=4,
            command=self._apply_demo_capital, cursor="hand2",
        ).pack(side="left")
        tk.Label(
            body,
            text="Resetuje kapitał DEMO do podanej kwoty (gdy jesteś w trybie DEMO i nie ma otwartych paper pozycji – albo wymusza start).",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=420, justify="left",
        ).pack(anchor="w", pady=(2, 4))

        _section("Powiadomienia Windows")
        _check("ALERTS_ENABLED", "Włącz alerty")
        _check("ALERT_PUSH", "Powiadomienia push / toast")
        _check("ALERT_SOUND", "Dźwięk")
        _check("ALERT_ON_OPEN", "Alert przy otwarciu")
        _check("ALERT_ON_CLOSE", "Alert przy zamknięciu")
        _check("ALERT_ON_MARGIN_CALL", "Alert margin call")
        _check("ALERT_ON_HALT", "Alert HALT / daily limit")
        _check("ALERT_ON_FEED_FAIL", "Alert problemy z feedem")

        _section("Filtry")
        _check("BLOCK_OB_THIN", "Blokuj cienki order book")
        _check("REQUIRE_PRIMARY_STRATEGY", "Wymagaj strategii 4h")
        _check("AGGRESSIVE_MODE", "Tryb agresywny")

        btn_row = tk.Frame(win, bg=CARD)
        btn_row.pack(fill="x", padx=16, pady=12)
        tk.Button(btn_row, text="Test powiadomienia", bg=BLUE, fg=BG,
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=6,
                  command=self._test_notify).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Zapisz wszystko", bg=GREEN, fg="#0b1220",
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=6,
                  command=self._save_all_settings).pack(side="left")
        tk.Button(btn_row, text="Zamknij", bg=CARD2, fg=TEXT,
                  font=("Segoe UI", 9), relief="flat", padx=10, pady=6,
                  command=win.destroy).pack(side="right")

        self.settings_status = tk.StringVar(value="")
        tk.Label(win, textvariable=self.settings_status, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(0, 12))
        self._secrets_visible = False
        try:
            self._reload_settings_ui()
        except Exception as e:
            print(f"[UI] reload settings: {e}")
        try:
            self._reload_secrets_ui()
        except Exception as e:
            print(f"[UI] reload secrets: {e}")
            self.secrets_status.set(f"Odczyt kluczy: {e}")
        self._refresh_mode_buttons()


    def _refresh_engine_buttons(self):
        """Podświetlenie przycisków Analiza / Handel / STOP wg stanu runtime."""
        try:
            rt = BotRuntime.get()
            analysis_on = bool(getattr(rt, "engine_enabled", False))
            trade_on = bool(getattr(rt, "trading_enabled", False))
            paper = bool(getattr(config, "PAPER_TRADING", True))
            mode = "DEMO" if paper else "LIVE"

            # Analiza
            if analysis_on:
                self.btn_analysis.config(
                    text="● Analiza ON",
                    bg="#2563eb", fg="#ffffff",
                    activebackground="#1d4ed8", activeforeground="#fff",
                )
            else:
                self.btn_analysis.config(
                    text="▶ Analiza",
                    bg="#1e3a5f", fg="#93c5fd",
                    activebackground=BLUE, activeforeground="white",
                )

            # Handel
            if trade_on:
                self.btn_trade.config(
                    text="● Handel ON",
                    bg="#16a34a", fg="#ffffff",
                    activebackground="#15803d", activeforeground="#fff",
                )
            else:
                self.btn_trade.config(
                    text="▶ Handel",
                    bg="#14532d", fg="#bbf7d0",
                    activebackground=GREEN, activeforeground="white",
                )

            # STOP – wyróżniony gdy coś działa
            if analysis_on or trade_on:
                self.btn_stop.config(
                    text="⏹ STOP",
                    bg="#7f1d1d", fg="#fecaca",
                    activebackground=RED, activeforeground="#fff",
                )
            else:
                self.btn_stop.config(
                    text="⏹ STOP",
                    bg=CARD2, fg=TEXT,
                    activebackground=BORDER, activeforeground=RED,
                )

            # Status label
            if trade_on:
                self.status_lbl.config(text=f"● {mode} · HANDEL", fg=GREEN)
            elif analysis_on:
                self.status_lbl.config(text=f"● {mode} · ANALIZA", fg=BLUE)
            else:
                self.status_lbl.config(text=f"● {mode} · STOP", fg=AMBER)
        except Exception:
            pass

    def _start_analysis(self):
        """Tylko cykle i sygnały — bez otwierania pozycji."""
        try:
            rt = BotRuntime.get()
            paper = bool(getattr(config, "PAPER_TRADING", True))
            # toggle: drugi klik przy włączonej analizie (bez handlu) → wyłącz
            if getattr(rt, "engine_enabled", False) and not getattr(rt, "trading_enabled", False):
                rt.stop_engine()
                self.alert_var.set("Analiza wyłączona")
                print("[UI] ANALYSIS_OFF")
            else:
                msg = rt.start_analysis()
                mode = "DEMO" if paper else "LIVE"
                self.alert_var.set(f"Analiza ON ({mode}) – handel OFF")
                print(f"[UI] {msg}")
            self._refresh_engine_buttons()
        except Exception as e:
            self.alert_var.set(f"Start analizy: {e}")

    def _start_trading(self):
        """Analiza + otwieranie pozycji."""
        try:
            rt = BotRuntime.get()
            paper = bool(getattr(config, "PAPER_TRADING", True))
            # toggle: drugi klik przy włączonym handlu → tylko handel OFF (analiza zostaje)
            if getattr(rt, "trading_enabled", False):
                msg = rt.stop_trading()
                self.alert_var.set("Handel OFF – analiza dalej działa")
                print(f"[UI] {msg}")
                self._refresh_engine_buttons()
                return
            if paper:
                try:
                    settings_store.update_setting("PAPER_TRADING", True)
                except Exception:
                    pass
                config.PAPER_TRADING = True
                risk = getattr(rt, "risk", None)
                if risk is not None and float(getattr(risk, "current_capital", 0) or 0) < 1:
                    try:
                        risk.apply_demo_capital()
                    except Exception:
                        risk.current_capital = float(config.STARTING_CAPITAL)
            msg = rt.start_trading()
            mode = "DEMO" if paper else "LIVE"
            self.alert_var.set(f"Handel ON ({mode})")
            print(f"[UI] {msg}")
            self._refresh_engine_buttons()
        except Exception as e:
            self.alert_var.set(f"Start handlu: {e}")

    def _start_engine(self):
        self._start_trading()

    def _stop_engine(self):
        try:
            rt = BotRuntime.get()
            msg = rt.stop_engine()
            self.uptime_var.set("⏱ 00:00:00")
            self.alert_var.set("Bot zatrzymany – analiza i handel OFF")
            print(f"[UI] {msg}")
            self._refresh_engine_buttons()
        except Exception as e:
            self.alert_var.set(f"Stop error: {e}")

    def _pause(self):
        self.rt.pause()
        self.status_lbl.config(text="● PAPER · PAUSED", fg=AMBER)

    def _resume(self):
        self.rt.resume()
        self.status_lbl.config(text="● PAPER · RUNNING", fg=GREEN)

    def _close_all(self):
        if messagebox.askyesno("Close All", "Na pewno zamknąć wszystkie otwarte pozycje?"):
            msg = self.rt.close_all()
            messagebox.showinfo("Close All", msg)

    def _on_close(self):
        if messagebox.askokcancel("Wyjście", "Zamknąć CryptoEdge i zatrzymać bota?"):
            self._stop_poll = True
            self.rt.running = False
            self.destroy()

    def _load_state(self) -> Optional[dict]:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
        return None

    def _set_kpi_color(self, key, value):
        try:
            n = float(value)
        except Exception:
            return
        _, _, lbl = self.kpi[key]
        if n > 0:
            lbl.config(fg=GREEN)
        elif n < 0:
            lbl.config(fg=RED)
        else:
            lbl.config(fg=TEXT)

    def _tick_clock(self):
        """Aktualizuje zegar co 1s i licznik do nastepnego odswiezenia UI."""
        if self._stop_poll:
            return
        now = datetime.now().strftime("%H:%M:%S")
        self.clock_var.set(now)
        self._countdown = max(0, self._countdown - 1)
        self.countdown_var.set(f"odśw. {self._countdown}s")
        # licznik dzialania bota
        try:
            eng = getattr(self.rt, "engine_enabled", False)
            started = getattr(self.rt, "started_at", None)
            if eng and started:
                sec = max(0, int(time.time() - started))
            else:
                sec = 0
            h, rem = divmod(sec, 3600)
            m, s = divmod(rem, 60)
            self.uptime_var.set(f"⏱ {h:02d}:{m:02d}:{s:02d}")
        except Exception:
            pass
        # stan przycisków Analiza / Handel
        try:
            self._refresh_engine_buttons()
        except Exception:
            pass
        try:
            self._refresh_mode_buttons()
        except Exception:
            pass
        self.after(1000, self._tick_clock)

    def _normalize_display_position(self, p: dict, mode: str = "DEMO") -> dict:
        """Ujednolica PaperTrader i read-only Blofin do jednego modelu UI.
        Nie tworzy danych, których źródło nie posiada — brak pola pozostaje None.
        """
        p = dict(p or {})
        direction = str(p.get("direction") or p.get("side") or "").upper()
        entry = p.get("entry", p.get("entry_price"))
        market = p.get("market", p.get("mark", p.get("mark_price")))
        pnl = p.get("pnl", p.get("unrealized_pnl"))
        try: entry_f = float(entry) if entry is not None else None
        except Exception: entry_f = None
        try: market_f = float(market) if market is not None else None
        except Exception: market_f = None
        try: pnl_f = float(pnl) if pnl is not None else 0.0
        except Exception: pnl_f = 0.0
        pnl_pct = p.get("pnl_pct")
        if pnl_pct is None and entry_f and market_f:
            pnl_pct = ((market_f - entry_f) / entry_f * 100.0)
            if direction == "SHORT": pnl_pct = -pnl_pct
        return {
            "id": p.get("id") or p.get("position_id"),
            "symbol": p.get("symbol") or p.get("inst_id") or "—",
            "direction": direction or "—",
            "entry": entry_f,
            "market": market_f,
            "pnl": pnl_f,
            "pnl_pct": pnl_pct,
            "margin": p.get("margin"),
            "margin_ratio": p.get("margin_ratio"),
            "sl": p.get("sl"),
            "tp": p.get("tp", p.get("tp_price")),
            "size": p.get("size", p.get("size_usd")),
            "age": p.get("age") or "—",
            "leverage": p.get("leverage"),
            "liquidation": p.get("liquidation"),
            "source": p.get("source") or mode,
        }

    def _poll(self):
        if self._stop_poll:
            return
        try:
            data = self._load_state() or {}
            r = data.get("risk") or self.rt.snapshot()
            cfg = data.get("config") or {}

            paused = bool(r.get("paused"))
            halted = bool(r.get("is_halted"))
            mode = str(data.get("mode") or r.get("mode") or ("PAPER" if getattr(config, "PAPER_TRADING", True) else "LIVE")).upper()
            if mode == "PAPER": mode = "DEMO"
            ex = data.get("exchange_account") or {}
            live = mode == "LIVE"
            try:
                self._refresh_mode_buttons()
            except Exception:
                pass
            eng = r.get("engine_enabled")
            if eng is None:
                eng = getattr(self.rt, "engine_enabled", False)
            if not eng:
                self.status_lbl.config(text=f"● {mode} · STOPPED", fg=AMBER)
            elif paused:
                self.status_lbl.config(text=f"● {mode} · PAUSED", fg=AMBER)
            elif halted:
                reason = (r.get("halt_reason") or "")[:40]
                self.status_lbl.config(text=f"● {mode} · HALTED {reason}", fg=RED)
            else:
                self.status_lbl.config(text=f"● {mode} · RUNNING", fg=GREEN)

            # DEMO = PaperTrader/risk; LIVE = read-only snapshot Blofin.
            if live:
                live_positions = list(ex.get("positions") or [])
                live_upnl = sum(float(p.get("pnl") or 0) for p in live_positions)
                live_eq = ex.get("equity")
                live_av = ex.get("available")
                self.kpi["capital"][0].set(money(live_eq))
                self.kpi["capital"][1].set("BLOFIN · LIVE")
                self.kpi["free"][0].set(money(live_av))
                self.kpi["free"][1].set("available z giełdy")
                self.kpi["used"][0].set(money((float(live_eq) - float(live_av)) if live_eq is not None and live_av is not None else 0))
                self.kpi["used"][1].set("equity − available")
                self.kpi["equity"][0].set(money(live_eq))
                self.kpi["equity"][1].set(f"uPnL {money(live_upnl)}")
                self.kpi["daily"][0].set(money(r.get("daily_pnl", 0)))
                self.kpi["daily"][1].set("lokalny ledger")
                self.kpi["open"][0].set(f"{len(live_positions)} / {cfg.get('max_positions', 5)}")
                self.kpi["open"][1].set("pozycje BLOFIN")
            else:
                self.kpi["capital"][0].set(money(r.get("capital")))
                self.kpi["capital"][1].set(f"DEMO · start {money(cfg.get('starting_capital', 10), 2)}")
                self.kpi["free"][0].set(money(r.get("free_margin", r.get("capital"))))
                self.kpi["free"][1].set("PaperTrader · dostępne")
                self.kpi["used"][0].set(money(r.get("used_margin", 0)))
                self.kpi["used"][1].set("margin paper")
                self.kpi["equity"][0].set(money(r.get("equity", r.get("capital"))))
                self.kpi["equity"][1].set(f"peak {money(r.get('peak_equity', 0))}")
                self.kpi["daily"][0].set(money(r.get("daily_pnl", 0)))
                self.kpi["daily"][1].set(pct(r.get("daily_loss_pct", 0)) + " dziennie")
                self.kpi["open"][0].set(f"{len(data.get('display_positions') or data.get('open_positions') or [])} / {cfg.get('max_positions', 5)}")
                u = r.get("unrealized_pnl", 0) or 0
                self.kpi["open"][1].set(f"uPnL {money(u)}")
            self._set_kpi_color("daily", r.get("daily_pnl", 0))
            self.kpi["dd"][0].set(f"{float(r.get('max_drawdown_pct') or 0):.1f}%")
            self.kpi["dd"][1].set("od szczytu equity")

            m = data.get("market") or {}
            fng = m.get("fear_greed") or {}
            g = m.get("global") or {}
            btc = data.get("btc_price")
            eth = data.get("eth_price")
            btc_ch = data.get("btc_change_24h")
            parts = []
            if btc is not None:
                parts.append(f"BTC  {money(btc, 2)}  ({pct(btc_ch)})")
            if eth is not None:
                parts.append(f"ETH  {money(eth, 2)}")
            if fng:
                parts.append(f"Fear&Greed  {fng.get('value', '—')} {fng.get('label', '')}")
            if g:
                parts.append(f"BTC.D {g.get('btc_dominance', '—')}%")
                parts.append(f"ALT.D {g.get('altcoin_dominance', '—')}%")
            reg = data.get("market_regime") or {}
            if reg.get("regime"):
                parts.insert(0, f"REŻIM  {str(reg.get('regime')).upper()}" + (f"  ATR×{reg.get('atr_ratio')}" if reg.get("atr_ratio") else ""))
            if data.get("sources"):
                parts.append("FEED  " + str(data.get("sources"))[:90])
            self.market_var.set("   ·   ".join(parts) if parts else "Oczekiwanie na pierwszy cykl danych…")
            universe = data.get("universe_size") or data.get("market_count") or "—"
            regime_name = str((data.get("market_regime") or {}).get("regime") or "—").replace("_", " ").upper()
            risk_level = "HALTED" if halted else ("PAUSED" if paused else "SAFE")
            if float(r.get("max_drawdown_pct") or 0) >= 10 or float(r.get("daily_loss_pct") or 0) >= 5:
                risk_level = "ELEVATED"
            self.ops_market_var.set(f"MARKETS  {universe}  ·  BTC {money(btc,2) if btc is not None else '—'}")
            self.ops_regime_var.set(f"REGIME  {regime_name}")
            self.ops_feed_var.set(f"DATA  cycle #{data.get('cycle', self.rt.cycle)}  ·  {str(data.get('sources') or 'feed —')[:45]}")
            self.ops_risk_var.set(f"RISK  {risk_level}  ·  DD {float(r.get('max_drawdown_pct') or 0):.1f}%")

            # Jedno źródło prawdy dla UI: API/state wybiera pozycje zależnie od DEMO/LIVE.
            # W LIVE pokazujemy rzeczywiste pozycje Blofin, w DEMO pozycje PaperTrader.
            display_positions = data.get("display_positions")
            if display_positions is None:
                display_positions = data.get("open_positions") or []
            display_source = data.get("position_source") or ("BLOFIN" if str(data.get("mode") or "").upper() == "LIVE" else "PAPER ENGINE")
            self._fill_pos(display_positions, source=display_source)
            self._fill_exchange(data.get("exchange_account") or {})
            # Scanner zawiera pełny obraz rynku; signals pozostaje listą aktywnych/top setupów.
            scanner = data.get("scanner_assets") or []
            self._fill_sig(scanner if scanner else (data.get("signals") or []))
            board = data.get("analysis_board") or []
            if not board:
                # fallback ze signals
                for s in (data.get("signals") or [])[:15]:
                    board.append({
                        "symbol": s.get("symbol"),
                        "direction": s.get("direction"),
                        "strength": s.get("strength"),
                        "decision": "KANDYDAT" if s.get("direction") not in (None, "NEUTRAL") else "POMINIĘTY",
                        "decision_why": ", ".join((s.get("reasons") or [])[:4]) if s.get("reasons") else "brak szczegółów w state",
                        "pros": list(s.get("reasons") or [])[:8],
                        "cons": [],
                        "price": s.get("price"),
                        "rsi": s.get("rsi"),
                        "macd": s.get("macd"),
                        "trend": s.get("trend"),
                        "change_24h": s.get("change_24h"),
                        "change_1h": s.get("change_1h"),
                        "strategy_pass": s.get("strategy_pass"),
                    })
            self._fill_analysis(board)
            self._closed_rows = list(data.get("closed_positions") or [])
            self._equity_history = list(data.get("equity_history") or [])
            self._fill_closed(self._closed_rows)
            self._update_health(data)

            met = data.get("metrics") or {}
            self.met_vars["trades"].set(str(met.get("trades", 0)))
            self.met_vars["wr"].set(f"{met.get('win_rate', 0)}%")
            self.met_vars["pf"].set(str(met.get("profit_factor", 0)))
            self.met_vars["net"].set(money(met.get("net_pnl", 0)))
            self.met_vars["avg_w"].set(money(met.get("avg_win", 0)))
            self.met_vars["avg_l"].set(money(met.get("avg_loss", 0)))
            if "exp" in self.met_vars:
                self.met_vars["exp"].set(money(met.get("expectancy", 0)))
            if "long" in self.met_vars:
                self.met_vars["long"].set(
                    f"{money(met.get('long_pnl', 0))} ({met.get('long_trades', 0)}t)"
                )
            if "short" in self.met_vars:
                self.met_vars["short"].set(
                    f"{money(met.get('short_pnl', 0))} ({met.get('short_trades', 0)}t)"
                )
            if hasattr(self, "side_perf_var"):
                self.side_perf_var.set(
                    f"LONG  {money(met.get('long_pnl',0))} · {met.get('long_trades',0)}T\n"
                    f"SHORT {money(met.get('short_pnl',0))} · {met.get('short_trades',0)}T\n"
                    f"TOTAL {money(met.get('net_pnl',0))} · {met.get('trades',0)}T · WR {met.get('win_rate',0)}%"
                )

            alerts = []
            # Inteligentne alerty: tylko zdarzenia wymagające uwagi, nie każdy tick.
            closed_now = data.get("closed_positions") or []
            new_closed = [x for x in closed_now if x.get("id") and x.get("id") not in self._seen_closed_ids]
            for x in new_closed:
                if x.get("id"): self._seen_closed_ids.add(x.get("id"))
                alerts.append(f"TRADE CLOSED · {x.get('symbol')} {pct(x.get('pnl_pct'))}")
            top = sorted(data.get("signals") or [], key=lambda x: float(x.get("strength") or 0), reverse=True)[:1]
            if top and float(top[0].get("strength") or 0) >= 0.80:
                alerts.append(f"SETUP ≥80 · {top[0].get('symbol')} {top[0].get('direction')} · {float(top[0].get('strength'))*100:.0f}/100")
            if halted:
                alerts.append(r.get("halt_reason") or "HALT")
            if paused:
                alerts.append("Nowe wejścia wstrzymane")
            corr = data.get("correlation") or {}
            if (corr.get("warning_count") or 0) >= 3:
                alerts.append(f"Rozjazdy cen: {corr.get('warning_count')}")
            src = str(data.get("sources") or "")
            if "ERROR" in src or "FAIL" in src:
                alerts.append("Problem ze źródłem danych")
            reserve = r.get("reserve_pct")
            if reserve:
                alerts.append(f"Rezerwa kapitału {reserve:.0f}%")
            dd = float(r.get("max_drawdown_pct") or 0)
            if dd >= 10:
                alerts.append(f"DD {dd:.1f}% — strefa ryzyka")
            if not alerts:
                alerts.append("SYSTEM OK  ·  nowe wejścia dozwolone" if not paused and not halted else "")
            self.alert_var.set("  |  ".join(x for x in alerts if x))
            eng = getattr(self.rt, "engine_enabled", False)
            if eng and getattr(self.rt, "started_at", None):
                up = int(time.time() - self.rt.started_at)
            else:
                up = 0
            h, rem = divmod(max(0, up), 3600)
            m, s = divmod(rem, 60)
            self.cycle_var.set(f"cykl #{data.get('cycle', self.rt.cycle)}  ·  praca {h:02d}:{m:02d}:{s:02d}")
            self._countdown = self._refresh_every
            self.countdown_var.set(f"odśw. {self._countdown}s")
        except Exception as e:
            self.status_lbl.config(text=f"● UI: {e}", fg=RED)
        self.after(self._refresh_every * 1000, self._poll)

    def _force_refresh(self):
        self._countdown = 0
        self._poll()

    def _select_tab(self, idx):
        try:
            self.nb.select(idx)
            for i, b in enumerate(getattr(self, "_nav_buttons", [])):
                if i == idx:
                    b.config(bg="#16324d", fg=TEXT)
                else:
                    b.config(bg="#0a111c", fg=MUTED)
        except Exception:
            pass

    def _toggle_pause(self):
        try:
            if getattr(self.rt, "risk", None) and getattr(self.rt.risk, "paused", False):
                self._resume()
            else:
                self._pause()
        except Exception:
            pass

    def _on_escape(self):
        try:
            for w in self.winfo_children():
                if isinstance(w, tk.Toplevel):
                    w.destroy()
        except Exception:
            pass

    def _pulse_status(self):
        try:
            self._pulse_on = not self._pulse_on
            current = self.status_lbl.cget("foreground")
            if "RUNNING" in str(self.status_lbl.cget("text")):
                self.status_lbl.configure(fg=GREEN if self._pulse_on else "#7be6ad")
        except Exception:
            pass
        self.after(650, self._pulse_status)

    def _open_layout_window(self):
        win = tk.Toplevel(self)
        win.title("CryptoEdge · Personalizacja dashboardu")
        win.configure(bg=CARD); win.geometry("360x430"); win.transient(self)
        tk.Label(win, text="WIDOCZNOŚĆ DASHBOARDU", bg=CARD, fg=BLUE, font=("Segoe UI",11,"bold")).pack(anchor="w", padx=16, pady=(16,4))
        tk.Label(win, text="Ukryj elementy, których nie potrzebujesz. Ustawienie działa od razu.", bg=CARD, fg=MUTED, font=("Segoe UI",8), wraplength=320, justify="left").pack(anchor="w", padx=16, pady=(0,10))
        labels={"capital":"Kapitał","free":"Wolne środki","used":"Zajęte środki","equity":"Equity","daily":"Daily PnL","open":"Pozycje","dd":"Max DD"}
        for key,label in labels.items():
            var=self._layout_vars.get(key)
            if var is None:
                var=tk.BooleanVar(value=True); self._layout_vars[key]=var
            var.set(bool(self.kpi_boxes.get(key) and self.kpi_boxes[key].winfo_ismapped()))
            tk.Checkbutton(win,text=label,variable=var,bg=CARD,fg=TEXT,selectcolor=CARD2,activebackground=CARD,activeforeground=TEXT,font=("Segoe UI",9),anchor="w",command=lambda k=key,v=var:self._set_kpi_visible(k,v)).pack(fill="x",padx=16,pady=2)
        tk.Button(win,text="Pokaż wszystko",bg=BLUE,fg=BG,relief="flat",font=("Segoe UI",9,"bold"),command=self._show_all_kpis).pack(pady=12)
        tk.Label(win,text="Skróty: F5 odśwież · P pauza/wznów · Ctrl+1..6 zakładki · Esc zamknij okno",bg=CARD,fg=MUTED,font=("Segoe UI",8),wraplength=320,justify="left").pack(padx=16,pady=8)

    def _show_all_kpis(self):
        for key in self.kpi_boxes:
            if key not in self._layout_vars:
                self._layout_vars[key]=tk.BooleanVar(value=True)
            self._layout_vars[key].set(True)
            self._set_kpi_visible(key,self._layout_vars[key])

    def _set_kpi_visible(self,key,var):
        box=self.kpi_boxes.get(key)
        if not box: return
        if bool(var.get()): box.pack(side="left",expand=True,fill="both",padx=5,pady=2)
        else: box.pack_forget()

    def _update_health(self,data):
        m=data.get("metrics") or {}
        trades=int(m.get("trades") or 0); wr=float(m.get("win_rate") or 0); pf=float(m.get("profit_factor") or 0)
        exp=float(m.get("expectancy") or 0); dd=float((data.get("risk") or {}).get("max_drawdown_pct") or 0)
        # Health to metryka jakości, nie sygnał handlowy.
        score=50.0
        score += min(20,max(-20,(pf-1)*20))
        score += min(15,max(-15,(wr-50)*0.3))
        score += min(10,max(-10,exp/max(abs(float((data.get("risk") or {}).get("capital") or 1)),1e-9)*1000))
        score -= min(20,dd*1.5)
        if trades < 10: score=min(score,65)
        score=max(0,min(100,score))
        state="HEALTHY" if score>=75 else "WATCH" if score>=55 else "DEGRADED"
        self.health_score_var.set(f"{score:.0f}/100")
        self.health_state_var.set(state)
        self.health_detail_var.set(f"PF {pf:.2f} · WR {wr:.1f}% · Exp {money(exp,4)} · DD {dd:.1f}% · {trades}T")
        color=GREEN if state=="HEALTHY" else AMBER if state=="WATCH" else RED
        try: self.health_state_var.set(state)
        except Exception: pass
        alerts=[]
        if trades<10: alerts.append("Mała próbka — nie oceniaj strategii po kilku transakcjach.")
        if pf and pf<1: alerts.append("Profit factor < 1 — strategia jest obecnie nierentowna w próbce.")
        if dd>=10: alerts.append(f"Drawdown {dd:.1f}% — podwyższone ryzyko.")
        if wr<40 and trades>=10: alerts.append("Win rate poniżej 40% — sprawdź expectancy i R:R.")
        self.health_alerts.set("  •  ".join(alerts) if alerts else "STRATEGY HEALTH OK · brak aktywnych ostrzeżeń jakości.")
        self._draw_equity()
        self._fill_replay(self._closed_rows)

    def _draw_equity(self):
        c=getattr(self,"eq_canvas",None)
        if c is None:return
        c.delete("all"); data=self._equity_history[-180:]
        if len(data)<2:
            c.create_text(12,20,anchor="nw",fill=MUTED,text="Brak wystarczającej historii equity — dane będą zbierane podczas pracy bota.",font=("Segoe UI",9)); return
        w=max(100,c.winfo_width()); h=max(120,c.winfo_height()); pad=28
        vals=[float(x.get("equity") or 0) for x in data]; dds=[float(x.get("drawdown_pct") or 0) for x in data]
        lo,hi=min(vals),max(vals); span=max(hi-lo,1e-9)
        pts=[]
        for i,v in enumerate(vals): pts += [pad+i*(w-2*pad)/max(len(vals)-1,1), pad+(hi-v)/span*(h-2*pad)]
        c.create_text(pad,8,anchor="nw",fill=MUTED,text=f"Equity  {money(vals[-1])}  · peak {money(hi)}",font=("Segoe UI",8))
        c.create_line(*pts,fill=BLUE,width=2,smooth=True)
        c.create_line(pad,h-pad,w-pad,h-pad,fill=BORDER)
        if max(dds)>0:
            c.create_text(w-pad, h-pad+5,anchor="se",fill=RED,text=f"DD max {max(dds):.1f}%",font=("Segoe UI",8))

    def _fill_replay(self,rows):
        if not hasattr(self,"replay_tree"):return
        self._clear(self.replay_tree)
        for p in rows[:25]:
            t=(p.get("exit_time") or ""); t=t.split("T")[-1][:8] if "T" in t else t[:8]
            risk=abs(float(p.get("entry") or 0)-float(p.get("sl") or p.get("entry") or 0))
            reward=abs(float(p.get("exit") or 0)-float(p.get("entry") or 0))
            rr=(reward/risk) if risk>0 else None
            tag="pos" if float(p.get("pnl_pct") or 0)>=0 else "neg"
            iid=self.replay_tree.insert("", "end", tags=(tag,), values=(t,p.get("symbol"),p.get("direction"),pct(p.get("pnl_pct")),f"{rr:.2f}" if rr is not None else "—"))
            self.replay_tree.set(iid,"sym",p.get("symbol") or "")
            self.replay_tree.item(iid,values=(t,p.get("symbol"),p.get("direction"),pct(p.get("pnl_pct")),f"{rr:.2f}" if rr is not None else "—"))

    def _on_replay_select(self,_evt=None):
        sel=self.replay_tree.selection()
        if not sel:return
        vals=self.replay_tree.item(sel[0],"values"); sym=vals[1] if len(vals)>1 else ""
        p=next((x for x in self._closed_rows if x.get("symbol")==sym),None)
        if not p:return
        self.replay_detail.set(
            f"{p.get('symbol')} {p.get('direction')} · Entry {p.get('entry')} → Exit {p.get('exit')}\n"
            f"SL {p.get('sl') or '—'} · TP {p.get('tp') or '—'} · Lev {p.get('leverage') or '—'} · Strength {p.get('strength') or '—'}\n"
            f"Engine {p.get('engine') or '—'} · Path {p.get('decision_path') or '—'} · PnL {money(p.get('pnl'))} ({pct(p.get('pnl_pct'))})\n"
            f"Powody: {', '.join(p.get('reasons') or []) or 'brak zapisanych powodów'}"
        )

    def _open_replay_from_history(self,_evt=None):
        try:
            sel=self.closed_tree.selection()
            if not sel:return
            vals=self.closed_tree.item(sel[0],"values")
            sym=vals[2] if len(vals)>2 else ""
            for i,p in enumerate(self._closed_rows):
                if p.get("symbol")==sym:
                    self.nb.select(5); break
        except Exception: pass

    def _clear(self, tree):
        for i in tree.get_children():
            tree.delete(i)

    def _ensure_tv_binds(self):
        if getattr(self, "_tv_bound", False):
            return
        try:
            self.pos_tree.bind("<Double-Button-1>", lambda e: self._open_tv(interval="240"))
            self.sig_tree.bind("<Double-Button-1>", lambda e: self._open_tv(interval="240"))
            self._tv_bound = True
        except Exception:
            pass

    def _fill_exchange(self, acc: dict):
        """Podgląd read-only konta Blofin (LIVE)."""
        try:
            if not hasattr(self, "exch_tree"):
                return
            self._clear(self.exch_tree)
            mode = (acc.get("mode") or "PAPER").upper()
            if mode != "LIVE":
                self.exch_info.set(
                    "Tryb DEMO – podgląd Blofin dostępny po przełączeniu na LIVE "
                    "(klucz tylko READ, bez trade)."
                )
                self.exch_tree.insert("", "end", values=("—", "przełącz LIVE", "", "", "", "", "", "", ""))
                return
            err = acc.get("error")
            eq = acc.get("equity")
            av = acc.get("available")
            src = acc.get("source") or "—"
            n = acc.get("positions_count")
            if n is None:
                n = len(acc.get("positions") or [])
            if err and eq is None:
                self.exch_info.set(f"LIVE · błąd odczytu: {err}")
            else:
                eq_s = f"${float(eq):,.4f}" if eq is not None else "—"
                av_s = f"${float(av):,.4f}" if av is not None else "—"
                self.exch_info.set(
                    f"READ-ONLY · źródło {src} · equity {eq_s} · available {av_s} · "
                    f"pozycje {n}" + (f" · {acc.get('positions_error')}" if acc.get("positions_error") else "")
                )
            rows = acc.get("positions") or []
            if not rows:
                self.exch_tree.insert("", "end", values=("—", "brak pozycji na giełdzie", "", "", "", "", "", "", ""))
                return
            for p in rows:
                pnl = float(p.get("pnl") or 0)
                tag = "pos" if pnl >= 0 else "neg"
                self.exch_tree.insert("", "end", tags=(tag,), values=(
                    p.get("direction") or "—",
                    p.get("symbol") or p.get("inst_id") or "—",
                    f"{float(p.get('size') or 0):.4g}",
                    f"{float(p.get('entry') or 0):.6g}" if p.get("entry") else "—",
                    f"{float(p.get('mark') or 0):.6g}" if p.get("mark") else "—",
                    f"{pnl:+.4f}",
                    f"{p.get('leverage'):.0f}x" if p.get("leverage") else "—",
                    f"{float(p.get('margin') or 0):.4f}" if p.get("margin") else "—",
                    f"{float(p.get('liquidation')):.6g}" if p.get("liquidation") else "—",
                ))
        except Exception as e:
            try:
                self.exch_info.set(f"UI exchange: {e}")
            except Exception:
                pass

    def _close_selected_position(self):
        sym = self._selected_symbol_from_pos()
        if not sym or sym in ("—", "brak pozycji"):
            messagebox.showinfo("Zamknij", "Zaznacz pozycję w tabeli.")
            return
        self._close_position_symbol(sym)

    def _close_position_symbol(self, symbol: str):
        symbol = (symbol or "").strip()
        if not symbol:
            return
        if not messagebox.askyesno("Zamknij pozycję", f"Zamknąć ręcznie {symbol} po cenie market?"):
            return
        try:
            msg = self.rt.close_symbol(symbol)
            self.alert_var.set(msg)
            print(f"[UI] {msg}")
        except Exception as e:
            messagebox.showerror("Zamknij", str(e))

    def _on_pos_click(self, event):
        """Klik w kolumnę Akcja → zamknij."""
        try:
            row = self.pos_tree.identify_row(event.y)
            col = self.pos_tree.identify_column(event.x)
            if not row:
                return
            # kolumna #11 = Akcja (1-indexed w Treeview)
            if col == "#11":
                vals = self.pos_tree.item(row, "values")
                sym = vals[1] if len(vals) > 1 else ""
                if sym and sym not in ("—", "brak pozycji"):
                    # lekkie opóźnienie, żeby selection się ustawił
                    self.after(10, lambda s=sym: self._close_position_symbol(s))
        except Exception:
            pass

    def _on_pos_right_click(self, event):
        try:
            row = self.pos_tree.identify_row(event.y)
            if row:
                self.pos_tree.selection_set(row)
            sym = self._selected_symbol_from_pos()
            if not sym or sym in ("—", "brak pozycji"):
                return
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label=f"Zamknij {sym}", command=lambda: self._close_position_symbol(sym))
            menu.add_command(label="TradingView 4H", command=lambda: self._open_tv(symbol=sym, interval="240"))
            menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _fill_pos(self, rows, source="DEMO"):
        self._ensure_tv_binds()
        self._clear(self.pos_tree)
        rows = [self._normalize_display_position(p, source) for p in (rows or [])]
        if not rows:
            self.pos_tree.insert("", "end", values=("—", "brak pozycji", "", "", "", "", "", "", "", "", ""))
            return
        for p in rows:
            pnl = float(p.get("pnl_pct") or 0)
            pnl_usd = float(p.get("pnl") or 0)
            entry = p.get("entry")
            sl = p.get("sl")
            direction = (p.get("direction") or "").upper()
            sl_locks_profit = False
            try:
                if sl is not None and entry:
                    sl_locks_profit = (direction == "LONG" and float(sl) > float(entry)) or (direction == "SHORT" and float(sl) < float(entry))
            except Exception:
                pass
            tag = "pos" if pnl >= 0 else "neg"
            sl_txt = (f"★ {float(sl):.6f}" if sl_locks_profit else f"{float(sl):.6f}") if sl is not None else "—"
            margin = p.get("margin")
            margin_txt = money(margin, 2) if margin is not None else "—"
            mr = p.get("margin_ratio")
            mr_txt = f"{float(mr)*100:.0f}%" if mr is not None else "—"
            age = p.get("age") or "—"
            self.pos_tree.insert("", "end", tags=(tag,), values=(
                direction, p.get("symbol") or "—", margin_txt, age,
                f"{float(entry):.6f}" if entry is not None else "—",
                f"{float(p.get('market')):.6f}" if p.get("market") is not None else "—",
                pct(pnl), f"${pnl_usd:+.4f}", mr_txt, sl_txt, "ZAMKNIJ",
            ))


    def _ana_row_label(self, c) -> str:
        """Linia listy: status + kierunek + symbol + siła."""
        status = (c.get("signal_status") or "").upper()
        if status == "OK":
            mark = "✓"
        elif status == "INEFFECTIVE":
            mark = "✗"
        else:
            mark = "·"
        direction = (c.get("direction") or "—")[:5]
        sym = c.get("symbol") or "?"
        st = c.get("strength")
        try:
            st_s = f"{float(st):.2f}" if st is not None else "—"
        except Exception:
            st_s = "—"
        return f"{mark} {direction:5} {sym:10} {st_s}"

    def _fill_analysis(self, cards):
        # sort A–Z, zachowaj zaznaczenie
        cards = list(cards or [])
        cards.sort(key=lambda x: str(x.get("symbol") or "").upper())
        self._ana_cards = cards
        self._filter_ana_list(preserve=True)

    def _filter_ana_list(self, preserve: bool = True):
        q = ""
        try:
            q = (self._ana_search_var.get() or "").strip().upper()
        except Exception:
            q = ""
        prev = self._ana_selected_sym if preserve else None
        if not prev:
            try:
                sel = self.ana_list.curselection()
                if sel and self._ana_cards_view:
                    prev = (self._ana_cards_view[sel[0]].get("symbol") or "").upper()
            except Exception:
                prev = None

        if q:
            view = [c for c in self._ana_cards if q in str(c.get("symbol") or "").upper()]
        else:
            view = list(self._ana_cards)
        self._ana_cards_view = view

        self.ana_list.delete(0, "end")
        if not view:
            self.ana_list.insert("end", "(brak monet – poczekaj na cykl)")
            try:
                self.ana_count_var.set("0 monet")
            except Exception:
                pass
            self._show_ana_detail(None)
            return

        select_idx = 0
        for i, c in enumerate(view):
            self.ana_list.insert("end", self._ana_row_label(c))
            if prev and str(c.get("symbol") or "").upper() == prev:
                select_idx = i

        n_ok = sum(1 for c in view if (c.get("signal_status") or "") == "OK")
        n_bad = sum(1 for c in view if (c.get("signal_status") or "") == "INEFFECTIVE")
        try:
            self.ana_count_var.set(f"{len(view)} monet · ✓{n_ok}  ✗{n_bad}")
        except Exception:
            pass

        self.ana_list.selection_clear(0, "end")
        self.ana_list.selection_set(select_idx)
        self.ana_list.see(select_idx)
        self._show_ana_detail(view[select_idx])
        self._ana_selected_sym = (view[select_idx].get("symbol") or "").upper()

    def _on_ana_select(self, _evt=None):
        sel = self.ana_list.curselection()
        if not sel or not self._ana_cards_view:
            return
        idx = sel[0]
        if idx >= len(self._ana_cards_view):
            return
        c = self._ana_cards_view[idx]
        self._ana_selected_sym = (c.get("symbol") or "").upper()
        self._show_ana_detail(c)



    def _selected_symbol_from_pos(self) -> str:
        try:
            sel = self.pos_tree.selection()
            if not sel:
                return ""
            vals = self.pos_tree.item(sel[0], "values")
            # columns: dir, sym, ...
            return (vals[1] if len(vals) > 1 else "") or ""
        except Exception:
            return ""

    def _selected_symbol_from_sig(self) -> str:
        try:
            sel = self.sig_tree.selection()
            if not sel:
                return ""
            vals = self.sig_tree.item(sel[0], "values")
            return (vals[1] if len(vals) > 1 else "") or ""
        except Exception:
            return ""

    def _selected_symbol_from_ana(self) -> str:
        try:
            idx = self.ana_list.curselection()
            if not idx:
                return self._ana_selected_sym or ""
            view = getattr(self, "_ana_cards_view", None) or self._ana_cards
            c = view[idx[0]]
            return c.get("symbol") or ""
        except Exception:
            return getattr(self, "_ana_selected_sym", None) or ""

    def _open_tv(self, symbol: str = "", interval: str = "240"):
        symbol = (symbol or "").strip()
        if not symbol:
            symbol = (
                self._selected_symbol_from_ana()
                or self._selected_symbol_from_pos()
                or self._selected_symbol_from_sig()
            )
        if not symbol or symbol in ("—", "brak pozycji", "?"):
            try:
                messagebox.showinfo("TradingView", "Zaznacz pozycję / sygnał / analizę (symbol).")
            except Exception:
                pass
            return
        url = open_chart(symbol, interval=interval)
        try:
            self.alert_var.set(f"TradingView: {symbol} ({interval}) → {url}")
        except Exception:
            pass

    def _reload_settings_ui(self):
        data = settings_store.apply_settings()
        for k, var in getattr(self, "_settings_vars", {}).items():
            if k not in data:
                continue
            if k == "STARTING_CAPITAL":
                var.set(str(float(data[k])))
            elif isinstance(settings_store.DEFAULTS.get(k), bool):
                var.set(bool(data[k]))
            else:
                var.set(data[k])
        self.settings_status.set("Załadowano settings.json")

    def _reload_secrets_ui(self):
        try:
            sec = secrets_store.load_secrets()
            for k, var in getattr(self, "_secret_vars", {}).items():
                var.set(sec.get(k) or "")
            if hasattr(self, "secrets_status"):
                self.secrets_status.set(secrets_store.status_label())
        except Exception as e:
            if hasattr(self, "secrets_status"):
                self.secrets_status.set(f"Błąd odczytu kluczy: {e}")

    def _save_secrets(self):
        try:
            data = {k: (var.get() or "").strip() for k, var in getattr(self, "_secret_vars", {}).items()}
            secrets_store.save_secrets(data)
            if hasattr(self, "secrets_status"):
                self.secrets_status.set("✓ " + secrets_store.status_label())
            self.settings_status.set("Klucze Blofin zapisane (secrets.bin + .env)")
            self.alert_var.set("Klucze API Blofin zaktualizowane")
        except Exception as e:
            self.settings_status.set(f"Błąd zapisu kluczy: {e}")
            messagebox.showerror("Klucze API", str(e))

    def _clear_secrets(self):
        if not messagebox.askyesno("Wyczyść klucze", "Usunąć zapisane klucze Blofin z tego komputera?"):
            return
        try:
            for var in getattr(self, "_secret_vars", {}).values():
                var.set("")
            secrets_store.save_secrets({k: "" for k in secrets_store.SECRET_KEYS})
            if hasattr(self, "secrets_status"):
                self.secrets_status.set(secrets_store.status_label())
            self.settings_status.set("Klucze wyczyszczone")
        except Exception as e:
            self.settings_status.set(f"Błąd: {e}")

    def _toggle_secret_visibility(self):
        self._secrets_visible = not getattr(self, "_secrets_visible", False)
        show = "" if self._secrets_visible else "*"
        for ent in getattr(self, "_secret_entries", []):
            try:
                ent.config(show=show)
            except Exception:
                pass

    def _on_setting_toggle(self, key, var):
        try:
            val = var.get()
            settings_store.update_setting(key, bool(val) if isinstance(settings_store.DEFAULTS.get(key), bool) else val)
            self.settings_status.set(f"Zapisano: {key} = {val}")
        except Exception as e:
            self.settings_status.set(f"Błąd: {e}")

    def _apply_demo_capital(self):
        """Zapisuje STARTING_CAPITAL i resetuje paper equity (gdy DEMO)."""
        try:
            raw = (self._demo_cap_var.get() if hasattr(self, "_demo_cap_var") else "100").strip().replace(",", ".")
            val = float(raw)
            if val < 1:
                raise ValueError("minimum 1$")
            if val > 1_000_000:
                raise ValueError("max 1 000 000$")
            settings_store.update_setting("STARTING_CAPITAL", val)
            config.STARTING_CAPITAL = val
            risk = getattr(self.rt, "risk", None)
            trader = getattr(self.rt, "trader", None)
            open_n = len(getattr(trader, "positions", []) or []) if trader else 0
            if open_n > 0 and bool(getattr(config, "PAPER_TRADING", True)):
                # tylko zapamiętaj start; nie kasuj otwartych pozycji
                if risk is not None:
                    risk.starting_capital = val
                    risk.paper_capital = val  # nowy „cel” startu; bieżący equity zostaje do zamknięcia
                self.settings_status.set(
                    f"Zapisano start DEMO ${val:.2f} (otwarte pozycje: {open_n} – equity nie resetowane)"
                )
                self.alert_var.set(f"Start DEMO ${val:.2f} zapisany; reset po zamknięciu pozycji / restart")
            else:
                if risk is not None:
                    risk.starting_capital = val
                    risk.paper_capital = val
                    risk.paper_peak_equity = val
                    if bool(getattr(config, "PAPER_TRADING", True)):
                        risk.current_capital = val
                        risk.peak_equity = val
                        risk.daily_start_capital = val
                        risk.daily_pnl = 0.0
                    try:
                        self.kpi["capital"][0].set(money(risk.current_capital))
                        self.kpi["equity"][0].set(money(risk.current_capital))
                    except Exception:
                        pass
                self.settings_status.set(f"Kapitał DEMO ustawiony na ${val:.2f}")
                self.alert_var.set(f"DEMO start ${val:.2f}")
                print(f"[UI] STARTING_CAPITAL → ${val:.2f}")
        except Exception as e:
            self.settings_status.set(f"Błąd salda DEMO: {e}")
            messagebox.showerror("Saldo DEMO", str(e))

    def _save_all_settings(self):
        data = {}
        for k, var in getattr(self, "_settings_vars", {}).items():
            if k == "STARTING_CAPITAL":
                try:
                    data[k] = float(str(var.get()).replace(",", "."))
                except Exception:
                    data[k] = float(getattr(config, "STARTING_CAPITAL", 100))
            else:
                data[k] = bool(var.get()) if isinstance(settings_store.DEFAULTS.get(k), bool) else var.get()
        full = settings_store.load_settings()
        full.update(data)
        settings_store.save_settings(full)
        settings_store.apply_settings(full)
        # także klucze jeśli coś wpisano
        try:
            if getattr(self, "_secret_vars", None):
                sec = {k: (var.get() or "").strip() for k, var in self._secret_vars.items()}
                if any(sec.values()):
                    secrets_store.save_secrets(sec)
                    if hasattr(self, "secrets_status"):
                        self.secrets_status.set("✓ " + secrets_store.status_label())
        except Exception as e:
            print(f"[UI] secrets on save-all: {e}")
        # zastosuj saldo DEMO jeśli podane
        try:
            if "STARTING_CAPITAL" in data:
                self._apply_demo_capital()
        except Exception:
            pass
        self.settings_status.set("Wszystkie ustawienia zapisane")

    def _test_notify(self):
        try:
            from alerts import notify
            notify("CryptoEdge test", "Powiadomienia Windows działają.", "info")
            self.settings_status.set("Wysłano testowe powiadomienie")
        except Exception as e:
            self.settings_status.set(f"Test alertu: {e}")

    def _update_ana_visual(self, c):
        """Odświeża wizualny panel: score → ścieżka → confluence."""
        if not c:
            self.ana_score_var.set("—")
            self.ana_decision_var.set("Oczekiwanie na analizę")
            self.ana_path_var.set("—")
            for _name, (var, lbl) in self.ana_component_labels.items():
                var.set("—")
                lbl.configure(bg=CARD2, fg=MUTED)
            self._draw_price_plan(None)
            return
        try:
            strength = float(c.get("strength") or 0)
            self.ana_score_var.set(f"{strength*100:.0f}/100")
        except Exception:
            self.ana_score_var.set("—")
        status = (c.get("signal_status") or "").upper()
        decision = c.get("decision") or "—"
        self.ana_decision_var.set(("✓ " if status == "OK" else "✗ " if status == "INEFFECTIVE" else "· ") + str(decision))
        self.ana_path_var.set(str(c.get("decision_path") or "NO_TRADE").replace("_", " "))

        direction = str(c.get("direction") or "NEUTRAL").upper()
        ind = c.get("indicators") or {}
        trend = str(c.get("trend") or ind.get("trend") or "—").upper()
        mtf = c.get("mtf_summary") or {}
        mtf_text = "MIXED"
        if mtf:
            vals = [str(v).upper() for v in mtf.values()]
            if any(direction in v and "✓" in v for v in vals): mtf_text = "ALIGN"
            elif any(("LONG" in v if direction == "SHORT" else "SHORT" in v) for v in vals): mtf_text = "CONFLICT"
        rsi = c.get("rsi") if c.get("rsi") is not None else ind.get("rsi")
        macd = str(c.get("macd") or ind.get("macd") or "").lower()
        mom = "NEUTRAL"
        if (direction == "LONG" and ("bull" in macd or (rsi is not None and 40 <= float(rsi) <= 65))) or (direction == "SHORT" and ("bear" in macd or (rsi is not None and 35 <= float(rsi) <= 60))):
            mom = "ALIGN"
        vf = str(c.get("vol_flag") or "").upper()
        volume = "GOOD" if any(x in vf for x in ("HIGH", "SPIKE")) else ("THIN" if any(x in vf for x in ("LOW", "THIN")) else "OK")
        fib = c.get("trend_fib") or {}
        fib_ok = bool(isinstance(fib, dict) and (fib.get("in_primary") or fib.get("in_deep")))
        fib_text = "ALIGN" if fib_ok else ("WATCH" if fib else "—")
        sd = c.get("source_divergence") or c.get("divergence") or c.get("source_div") or {}
        if isinstance(sd, dict): sdv = sd.get("max_diff_pct")
        else: sdv = None
        div_text = "CLEAN" if sdv is not None and float(sdv) < 0.5 else ("WATCH" if sdv is not None else "—")
        liq = c.get("liquidity") or {}
        liq_score = liq.get("score") if isinstance(liq, dict) else None
        liq_text = f"{float(liq_score):.0f}" if liq_score is not None else "—"
        vals = {"Trend": trend[:10], "MTF": mtf_text, "Momentum": mom, "Volume": volume, "Fibonacci": fib_text, "Divergence": div_text, "Liquidity": liq_text}
        good = {"ALIGN", "GOOD", "CLEAN"}
        for name, value in vals.items():
            var, lbl = self.ana_component_labels[name]
            var.set(value)
            if value in good or (name == "Trend" and ((direction == "LONG" and "UP" in value) or (direction == "SHORT" and "DOWN" in value))):
                lbl.configure(bg="#123524", fg=GREEN)
            elif value in {"CONFLICT", "THIN", "WATCH"}:
                lbl.configure(bg="#3a2b12", fg=AMBER)
            elif value == "—":
                lbl.configure(bg=CARD2, fg=MUTED)
            else:
                lbl.configure(bg="#211d2d", fg=PURPLE)
        self._draw_price_plan(c)

    def _calc_rr(self,c):
        try:
            e=float(c.get("price")); sl=float(c.get("sl_price")); tp=float(c.get("tp_price")); r=abs(e-sl)
            return abs(tp-e)/r if r>0 else 0.0
        except Exception:
            return 0.0

    def _draw_price_plan(self, c):
        cv=getattr(self,"ana_plan_canvas",None)
        if cv is None:return
        cv.delete("all")
        if not c:
            cv.create_text(10,10,anchor="nw",fill=MUTED,text="Wybierz setup, aby zobaczyć plan ceny.",font=("Segoe UI",8)); return
        try:
            entry=float(c.get("price")); sl=float(c.get("sl_price")); tp=float(c.get("tp_price"))
        except Exception:
            cv.create_text(10,10,anchor="nw",fill=MUTED,text="Brak pełnych danych Entry / SL / TP.",font=("Segoe UI",8)); return
        vals=[entry,sl,tp]
        fib=c.get("trend_fib") or {}; fmap=fib.get("map") if isinstance(fib,dict) else {}
        levels=fmap.get("levels") if isinstance(fmap,dict) else {}
        for k,v in (levels or {}).items():
            try: vals.append(float(v))
            except Exception: pass
        lo,hi=min(vals),max(vals); span=max(hi-lo,1e-9); w=max(300,cv.winfo_width()); h=max(90,cv.winfo_height()); x1=70; x2=w-20
        def y(v): return 18+(hi-v)/span*(h-38)
        # grid + fib levels
        for k,v in sorted((levels or {}).items(), key=lambda kv: float(kv[0]) if str(kv[0]).replace('.','',1).isdigit() else 99):
            try:
                yy=y(float(v)); cv.create_line(x1,yy,x2,yy,fill="#3b4b63",dash=(2,3)); cv.create_text(8,yy,anchor="w",fill=MUTED,text=f"FIB {float(k):.3f}",font=("Segoe UI",7))
            except Exception: pass
        for label,v,color in (("TP",tp,GREEN),("ENTRY",entry,BLUE),("SL",sl,RED)):
            yy=y(v); cv.create_line(x1,yy,x2,yy,fill=color,width=2); cv.create_text(x1+4,yy-9,anchor="w",fill=color,text=f"{label}  {v:.6f}",font=("Segoe UI",8,"bold"))
        risk=abs(entry-sl); reward=abs(tp-entry); rr=reward/risk if risk>0 else 0
        cv.create_text(w-20,h-6,anchor="se",fill=TEXT,text=f"R:R {rr:.2f}  ·  {c.get('direction') or '—'}",font=("Segoe UI",8,"bold"))

    def _show_ana_detail(self, c):
        self._update_ana_visual(c)
        self.ana_detail.configure(state="normal")
        self.ana_detail.delete("1.0", "end")
        if not c:
            self.ana_detail.insert("end", "Wybierz monetę z listy (A–Z), aby zobaczyć analizę.\n", "muted")
            self.ana_detail.configure(state="disabled")
            return

        # flatten indicators nested
        ind = c.get("indicators") or {}
        for k, v in ind.items():
            if c.get(k) is None and v is not None:
                c[k] = v
        if c.get("pros") is None:
            c["pros"] = c.get("for") or []
        if c.get("cons") is None:
            c["cons"] = c.get("against") or []

        status = (c.get("signal_status") or "").upper()
        dec = c.get("decision") or "?"
        if status == "OK":
            tag = "dec_ok"
            status_line = "✓ SYGNAŁ EFEKTYWNY – kwalifikuje się do otwarcia"
        elif status == "INEFFECTIVE":
            tag = "dec_no"
            status_line = "✗ NIEFEKTYWNA JAKO SYGNAŁ – bot nie weźmie tej pozycji"
        else:
            tag = "dec_mid"
            status_line = "· BRAK SYGNAŁU – moneta tylko obserwowana"

        self.ana_detail.insert("end", f"{c.get('symbol') or '?'}  {c.get('direction') or '—'}\n", "title")
        self.ana_detail.insert("end", status_line + "\n", tag)
        summary = c.get("signal_summary") or c.get("decision_why") or "—"
        self.ana_detail.insert("end", f"Podsumowanie: {summary}\n", "muted")
        path = c.get("decision_path") or "—"
        self.ana_detail.insert("end", f"Decyzja silnika: {dec}   ·   Ścieżka: {path}\n\n", "muted")

        self.ana_detail.insert("end", "── Dane rynkowe ──\n", "title")
        lines = [
            f"Cena:        {c.get('price')}",
            f"Siła:        {c.get('strength')}",
            f"1h / 24h / 7d: {c.get('change_1h')}% / {c.get('change_24h')}% / {c.get('change_7d')}%",
            f"RSI:         {c.get('rsi')}",
            f"MACD:        {c.get('macd')}",
            f"Trend:       {c.get('trend')}",
            f"ATR%:        {c.get('atr_pct')}",
            f"Volume 24h:  {c.get('volume_24h')}  ({c.get('vol_flag') or '—'})",
            f"Strategia:   pass={c.get('strategy_pass')} dir={c.get('strategy_direction')} ADX={c.get('strategy_adx')}",
            f"Entry / SL / TP: {c.get('price')} / {c.get('sl_price')} / {c.get('tp_price')}",
            f"R:R:         {self._calc_rr(c):.2f}",
            f"Dywergencja: {c.get('source_divergence') or c.get('divergence') or '—'}",
            f"Fibonacci:   {c.get('fib_level') or c.get('fibonacci') or c.get('trend_fib') or '—'}",
        ]
        liq = c.get("liquidity") or {}
        if liq:
            lines.append(
                f"Płynność:    {liq.get('score', '?')}/100  ocena {liq.get('grade', '?')} ({liq.get('label', '—')})"
            )
            parts = liq.get("parts") or {}
            if parts:
                lines.append(
                    f"  vol={parts.get('volume',0)} depth={parts.get('depth',0)} "
                    f"spread={parts.get('spread',0)} sources={parts.get('sources',0)}"
                )
            for n in (liq.get("notes") or [])[:4]:
                lines.append(f"  · {n}")
        mtf = c.get("mtf_summary") or {}
        if mtf:
            lines.append("MTF:         " + " | ".join(f"{k}={v}" for k, v in mtf.items()))
        for ln in lines:
            self.ana_detail.insert("end", ln + "\n", "muted")

        self.ana_detail.insert("end", "\n── ZA otwarciem (+) ──\n", "title")
        pros = c.get("pros") or []
        if not pros:
            self.ana_detail.insert("end", "  (brak)\n", "muted")
        for p in pros:
            self.ana_detail.insert("end", f"  + {p}\n", "pro")

        self.ana_detail.insert("end", "\n── PRZECIW otwarciu (−) ──\n", "title")
        cons = c.get("cons") or []
        if not cons:
            self.ana_detail.insert("end", "  (brak)\n", "muted")
        for p in cons:
            self.ana_detail.insert("end", f"  − {p}\n", "con")

        if c.get("reject_reason"):
            self.ana_detail.insert("end", f"\nFiltr odrzucenia: {c.get('reject_reason')}\n", "dec_no")
        if status == "INEFFECTIVE":
            self.ana_detail.insert(
                "end",
                "\n── Wniosek ──\n"
                "Ta moneta jest obecnie nieefektywna jako sygnał handlowy.\n"
                "Powody powyżej (PRZECIW / filtr). Bot jej nie otworzy,\n"
                "dopóki warunki się nie poprawią.\n",
                "dec_no",
            )

        self.ana_detail.configure(state="disabled")

    def _fill_sig(self, rows):
        trees = [t for t in (getattr(self, "sig_tree", None), getattr(self, "overview_sig_tree", None)) if t is not None]
        for tree in trees:
            self._clear(tree)
        if not rows:
            for tree in trees:
                tree.insert("", "end", values=("—", "brak danych", "", "", "", "", "", "", "", ""))
            return
        # Nie wymyślamy confidence/score. strength jest źródłową wartością silnika.
        rows = sorted(rows, key=lambda x: float(x.get("strength") or 0), reverse=True)
        for s in rows[:150]:
            direction = str(s.get("direction") or s.get("signal_status") or "NEUTRAL").upper()
            tag = "long" if direction == "LONG" else "short" if direction == "SHORT" else "neutral"
            strength = s.get("strength")
            try: score = f"{float(strength)*100:.1f}" if strength is not None else "—"
            except Exception: score = "—"
            mtf = s.get("mtf_summary") or {}
            mtf_txt = " / ".join(f"{k}:{v}" for k,v in mtf.items()) if mtf else "—"
            strategy = s.get("strategy_pass")
            strategy_txt = "PASS" if strategy is True else "FAIL" if strategy is False else "—"
            path = s.get("decision_path") or "—"
            rr = "—"
            try:
                entry = float(s.get("price")) if s.get("price") is not None else None
                sl = float(s.get("sl_price")) if s.get("sl_price") is not None else None
                tp = float(s.get("tp_price")) if s.get("tp_price") is not None else None
                if entry and sl and tp and abs(entry-sl)>0:
                    rr = f"{abs(tp-entry)/abs(entry-sl):.2f}"
            except Exception: pass
            why = s.get("decision_why") or s.get("signal_summary") or ", ".join((s.get("reasons") or [])[:2]) or "—"
            values=(direction, s.get("symbol") or "—", score, pct(s.get("change_24h")), s.get("trend") or "—", path, rr, strategy_txt, mtf_txt, why)
            for tree in trees:
                tree.insert("", "end", tags=(tag,), values=values)


    def _fill_closed(self, rows):

        self._clear(self.closed_tree)
        if not rows:
            self.closed_tree.insert("", "end", values=("—", "", "brak historii", "", "", "", ""))
            return
        for p in rows:
            t = (p.get("exit_time") or "")
            if "T" in t:
                t = t.split("T")[-1][:8]
            pnl = float(p.get("pnl_pct") or 0)
            tag = "pos" if pnl >= 0 else "neg"
            self.closed_tree.insert("", "end", tags=(tag,), values=(
                t,
                (p.get("direction") or "") + ("½" if p.get("partial_taken") else ""),
                p.get("symbol"),
                f"{float(p.get('entry') or 0):.6f}",
                f"{float(p.get('exit') or 0):.6f}" if p.get("exit") is not None else "—",
                pct(pnl),
                money(p.get("pnl")),
            ))


def run_native_ui(rt: BotRuntime):
    app = CryptoEdgeApp()
    app.mainloop()
    rt.running = False
