#!/usr/bin/env python3
"""English translation of frozen Chinese Section V Figures 5 and 6.

Data, colors, line styles, ranges and annotations are retained.
Figure 5 uses the authorized single-column two-panel layout; Figure 6
uses the approved single-column 2+1 geometry (F-FIG6-SINGLE-COLUMN-20260928). All figure observations are in the adjacent
section5_figure_data.json. No optimizer or archived source tree is required.
Run: python draw_section5_english.py [--roman-font PATH]
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.font_manager import FontProperties
from matplotlib.offsetbox import AnchoredOffsetbox, HPacker, TextArea
from matplotlib.ticker import FuncFormatter, NullLocator
import numpy as np
OUT = Path(__file__).resolve().parent
FROZEN_CHINESE_SHA256 = {'draw_section5_figures.py': 'f356a438ddc84bc0c84afc0ca9eada6dc9ece3be4cc0945ded4d70981093169c', 'figure_data.json': '213550688b19447f4d328d15a9f42131a5fcb5683ae4889245b41d4577d4e072', 'Figure5_CN_v01.png': '646dbbfef8c603b40ff86ae739f40b263334152e1c8df48ee9332475c36bfeb5', 'Figure5_CN_v01.svg': '526cd1ea5023df04e1a08a89191a8bcb9d5a1bbdaf163b195b022b000d0e21e4', 'Figure5_CN_v01.pdf': 'eaa03811ea400406178a31089b94bd15ea54602db23f6da811d858f303df9a67', 'Figure6_CN_v01.png': '3f6c1f46eaac59d0dca46c54fef710eb8cdb5143e9b5e435f449eeaeec299c21', 'Figure6_CN_v01.svg': 'b04f2856a291ee32c36a7af8f944f6781cf73939b265fcabfee621f0c7c81e3b', 'Figure6_CN_v01.pdf': '503f9059dfa9590035f58f48766351cf3e44427b530eeb0f1abd38a4e189c037'}
TOL = 1e-8
BASELINES = ("path", "one_branch", "contiguous", "greedy")
LABELS = dict(path="Path", one_branch="One branch", contiguous="Contiguous", greedy="Greedy")
COLORS = dict(path="#1769D2", one_branch="#D88900", contiguous="#8854BC", greedy="#009E73")
STYLES = dict(path="-", one_branch="--", contiguous="-.", greedy=(0, (1.2, 1.3)))
INK = "#142B40"
MUTED = "#485F73"

def setup_fonts(roman_font):
    font_manager.fontManager.addfont(str(roman_font))
    roman = FontProperties(fname=str(roman_font)).get_name()
    plt.rcParams.update({
        "font.family": [roman], "font.size": 8.2,
        "axes.titlesize": 8.5, "axes.labelsize": 8.2,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
        "text.color": INK, "axes.labelcolor": INK, "axes.edgecolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.linewidth": .6, "xtick.major.width": .6, "ytick.major.width": .6,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "svg.fonttype": "path", "pdf.fonttype": 42, "ps.fonttype": 42,
        "axes.unicode_minus": False, "mathtext.fontset": "cm",
        "axes.spines.top": False, "axes.spines.right": False,
    })
    return {"roman": roman, "font_size_pt": [8.0, 8.5]}

def axes_style(ax):
    ax.grid(which="major", color="#D7DFE6", linewidth=.48, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", pad=2.2)

def mathematical_xlabel(ax, words, symbol):
    """Keep Roman labels and mathematical symbols typographically consistent."""
    words_box = TextArea(words + " ", textprops={"size": 8.2, "color": INK})
    symbol_box = TextArea("$" + symbol + "$", textprops={"size": 8.2, "color": INK})
    label = HPacker(children=[words_box, symbol_box], align="baseline", pad=0, sep=0)
    anchor = AnchoredOffsetbox(loc="upper center", child=label, pad=0, borderpad=0,
                              bbox_to_anchor=(.5, -.142), bbox_transform=ax.transAxes,
                              frameon=False)
    ax.add_artist(anchor)

def save(fig, stem):
    fig.canvas.draw()
    for ext in ("png", "svg", "pdf"):
        kwargs = {"dpi": 400} if ext == "png" else {}
        fig.savefig(OUT / f"{stem}.{ext}", facecolor="white", **kwargs)
    plt.close(fig)

def draw5(data):
    original_rc = plt.rcParams.copy()
    # Single-column layout authorized 2026-09-28; retain all observations.
    plt.rcParams.update({"font.size": 7.3, "axes.titlesize": 7.5, "axes.labelsize": 7.3,
                         "xtick.labelsize": 7.1, "ytick.labelsize": 7.1, "legend.fontsize": 7.0})
    fig, axes = plt.subplots(1, 2, figsize=(3.5, 2.50))
    fig.subplots_adjust(left=.135, right=.955, bottom=.16, top=.52, wspace=.60)
    descriptors = [("A", "independent", "A · Independent", "#1769D2", "-", "o"),
                   ("A", "correlated", "A · Positive corr.", "#1769D2", "--", "s"),
                   ("B", "independent", "B · Independent", "#009E73", "-", "o"),
                   ("B", "correlated", "B · Positive corr.", "#009E73", "--", "s")]
    for ax, field, ylabel, title in zip(axes, ["process_wall_seconds", "peak_rss_mib"], ["Wall-clock time (s)", "Peak RSS (MiB)"], ["(a) Time", "(b) Memory"]):
        for profile, workload, label, color, style, marker in descriptors:
            cases = sorted([r for r in data["scan_cases"] if r["profile"] == profile and r["workload"] == workload], key=lambda r: r["T"])
            x = np.array([r["T"] for r in cases]); a = np.array([r[field] for r in cases])
            med = np.median(a, axis=1); low = a.min(axis=1); high = a.max(axis=1)
            ax.errorbar(x, med, yerr=[med-low, high-med], label=label, color=color,
                        linestyle=style, marker=marker, markersize=2.7, linewidth=1.05,
                        markerfacecolor="white" if workload == "correlated" else color,
                        markeredgewidth=.9, elinewidth=.7, capsize=2, capthick=.7, zorder=3)
        ax.set_xscale("log", base=2)
        ax.set_xticks([20, 40, 80, 160]); ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
        ax.set_xlim(18, 177)
        if field == "process_wall_seconds":
            ax.set_yscale("log"); ax.set_ylim(.1, 13)
            ax.set_yticks([.1, 1, 10]); ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
            ax.yaxis.set_minor_locator(NullLocator())
        else:
            ax.set_ylim(12, 67); ax.set_yticks([20, 40, 60])
        ax.set_xlabel(r"Threshold count $T$", labelpad=3)
        ax.set_ylabel(ylabel, labelpad=2.5)
        ax.set_title(title, loc="left", pad=5)
        axes_style(ax)
    # External zoom windows share the original data, colors and error bars.
    from matplotlib.patches import Rectangle
    for parent, field, limits, ticks in zip(axes,
            ["process_wall_seconds", "peak_rss_mib"],
            [(.185, .255), (17.30, 17.58)], [[.20, .24], [17.35, 17.50]]):
        pos = parent.get_position()
        zoom = fig.add_axes([pos.x0, .675, pos.width, .14])
        for profile, workload, label, color, style, marker in descriptors[:2]:
            cases = sorted([r for r in data["scan_cases"] if r["profile"] == profile and r["workload"] == workload], key=lambda r: r["T"])
            x = np.array([r["T"] for r in cases]); values = np.array([r[field] for r in cases])
            med = np.median(values, axis=1)
            zoom.errorbar(x, med, yerr=[med-values.min(axis=1), values.max(axis=1)-med],
                          color=color, linestyle=style, marker=marker, markersize=2.7,
                          markerfacecolor="white" if workload == "correlated" else color,
                          linewidth=1.05, markeredgewidth=.9, elinewidth=.7, capsize=2, capthick=.7)
        zoom.set_xscale("log", base=2); zoom.set_xlim(75, 85); zoom.set_ylim(*limits)
        zoom.set_xticks([80]); zoom.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
        zoom.set_yticks(ticks); zoom.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:.2f}"))
        zoom.xaxis.set_minor_locator(NullLocator()); zoom.yaxis.set_minor_locator(NullLocator())
        zoom.tick_params(labelsize=6.5, pad=1.5, length=1.5)
        zoom.set_title("A near T=80", fontsize=6.8, pad=2)
        for spine in zoom.spines.values():
            spine.set_visible(True); spine.set_linewidth(.55); spine.set_color(MUTED)
        parent.add_patch(Rectangle((75, limits[0]), 10, limits[1]-limits[0],
                                  fill=False, edgecolor=MUTED, linewidth=.65, linestyle=":"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.54, 1.005), ncol=2,
               frameon=False, handlelength=1.9, columnspacing=1.2, handletextpad=.5)
    save(fig, "figure5_threshold_scaling")
    plt.rcParams.update(original_rc)

def survival_points(values):
    positive = np.array(sorted(100 * v for v in values if v > 0))
    xs, counts = np.unique(positive, return_counts=True)
    ys = np.cumsum(counts[::-1])[::-1] / len(values) * 100
    return xs, ys

def draw6(data):
 plt.rcParams.update({'font.size':7.3,'axes.titlesize':7.5,'axes.labelsize':7.3,
                     'xtick.labelsize':7.0,'ytick.labelsize':7.0,'legend.fontsize':7.0})
 fig=plt.figure(figsize=(3.5,3.6))
 a=fig.add_axes([.155,.545,.365,.275])
 b=fig.add_axes([.605,.545,.365,.275],sharey=a)
 c=fig.add_axes([.155,.10,.815,.295])
 endpoints={}
 for ax,suite,title in [(a,'structure','(a) Structure suite'),(b,'s1','(b) S1 suite')]:
  rows=[r for r in data['loss_records'] if r['suite']==suite]
  for method in BASELINES:
   xs,ys=survival_points([r['normalized_loss'][method] for r in rows])
   ax.step(np.r_[.003,xs],np.r_[ys[0],ys],where='pre',label=LABELS[method],
           color=COLORS[method],linestyle=STYLES[method],linewidth=1.05,zorder=3)
   ax.plot(xs[-1],ys[-1],marker='o',ms=2.8,color=COLORS[method],zorder=4)
   endpoints[suite+'_'+method]={'x':float(xs[-1]),'y':float(ys[-1])}
  ax.set_xscale('log');ax.set_yscale('log')
  ax.set_xlim(.003,60);ax.set_ylim(.085,125)
  ax.set_xticks([.01,.1,1,10]);ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x:g}'))
  ax.set_yticks([.1,1,10,100]);ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x:g}'))
  ax.xaxis.set_minor_locator(NullLocator());ax.yaxis.set_minor_locator(NullLocator())
  ax.set_title(title,loc='left',pad=5)
  axes_style(ax)
 b.tick_params(axis='y',labelleft=False)
 a.set_ylabel('Fraction of instances (%)',labelpad=3)
 fig.text(.56,.458,'Normalized profit loss (%)',ha='center',va='center',fontsize=7.3)
 gx,gy=survival_points([r['normalized_loss']['greedy'] for r in data['loss_records'] if r['suite']=='structure'])
 assert len(gx)==1 and abs(gy[0]-100/750)<1e-12
 a.annotate('1/750',xy=(gx[0],gy[0]),xytext=(.12,.30),fontsize=7.2,
            color=COLORS['greedy'],va='bottom',
            arrowprops={'arrowstyle':'-','lw':.6,'color':COLORS['greedy']})
 handles,labels=a.get_legend_handles_labels();order=[0,2,1,3]
 fig.legend([handles[i] for i in order],[labels[i] for i in order],loc='upper center',
            bbox_to_anchor=(.55,1.0),ncol=2,title='Methods (a,b)',title_fontsize=7.2,
            frameon=False,handlelength=2.0,columnspacing=1.3,handletextpad=.5,labelspacing=.35)
 arr=100*np.array([r['normalized_gap'] for r in data['capacity_records']]);k=np.arange(1,7)
 assert np.all(arr[:,6:]==0)
 p95=np.quantile(arr,.95,axis=0,method='linear');maximum=arr.max(axis=0)
 for name,series,color,style,marker in [
  ('Median',np.quantile(arr,.5,axis=0,method='linear'),'#1769D2','-','o'),
  ('95th percentile',p95,'#D88900','--','s'),('Maximum',maximum,'#009E73','-.','^')]:
  c.plot(k,series[k],label=name,color=color,linestyle=style,marker=marker,
         linewidth=1.1,markersize=3.1,markerfacecolor='white',markeredgewidth=.85,zorder=3)
 c.set_xlim(.78,6.18);c.set_ylim(-2.2,60)
 c.set_xticks([1,2,3,4,5,6]);c.set_yticks([0,20,40,60])
 c.set_xlabel('Catalog capacity $k$',labelpad=3)
 c.set_ylabel('Normalized profit gap (%)',labelpad=3)
 c.set_title('(c) S1 capacity frontier',loc='left',pad=5)
 axes_style(c)
 c.legend(loc='upper right',frameon=False,handlelength=2,handletextpad=.45,
          labelspacing=.28,borderpad=.15,borderaxespad=.2)
 for value,xy,xytext,color in [
  (maximum[4],(4,maximum[4]),(4.45,23.0),'#009E73'),
  (p95[4],(4,p95[4]),(4.60,13.8),'#D88900'),
  (maximum[5],(5,maximum[5]),(5.15,5.0),'#009E73')]:
  c.annotate(f'{value:.2f}%',xy=xy,xytext=xytext,fontsize=7.1,color=color,
             ha='left',va='bottom',arrowprops={'arrowstyle':'-','lw':.6,'color':color,'shrinkA':1.2,'shrinkB':2.3})
 save(fig, 'figure6_catalog_capacity')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roman-font", type=Path, default=OUT / "figure_nimbus_roman.ttf")
    args = parser.parse_args()
    data_path = OUT / "section5_figure_data.json"
    assert hashlib.sha256(data_path.read_bytes()).hexdigest() == FROZEN_CHINESE_SHA256["figure_data.json"]
    data = json.loads(data_path.read_text())
    assert len(data["scan_cases"]) == 16
    assert sum(len(r["process_wall_seconds"]) for r in data["scan_cases"]) == 48
    assert len(data["loss_records"]) == 1398 and len(data["capacity_records"]) == 648
    setup_fonts(args.roman_font)
    draw5(data)
    setup_fonts(args.roman_font)
    draw6(data)
    print(json.dumps({"status": "PASS", "source_data_sha256": FROZEN_CHINESE_SHA256["figure_data.json"],
                      "figures": ["figure5_threshold_scaling", "figure6_catalog_capacity"],
                      "dimensions_inches": {"figure5": [3.5, 2.50], "figure6": [3.5, 3.6]}, "figure5_font_size_pt": [7.0, 7.5]}))

if __name__ == "__main__":
    main()
