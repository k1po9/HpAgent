import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import gsap from "gsap";
import { Flip } from "gsap/Flip";
import { useGSAP } from "@gsap/react";
import "./style.css";
import { makeParticles, createParticlePainter } from "./particles";

gsap.registerPlugin(Flip, useGSAP);

function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setReduced(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return reduced;
}

function Scene({ mode, duration, quality, forceReduced, onBusy, onResult }) {
  const root = useRef(null);
  const canvas = useRef(null);
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(false);
  const [recovery, setRecovery] = useState("idle");
  const [scenario, setScenario] = useState("success");
  const ring = mode === "recovery" && recovery !== "expired";
  useEffect(() => {
    if (recovery !== "checking") return;
    const timer = setTimeout(
      () => {
        if (scenario === "error" || scenario === "expired") {
          setRecovery(scenario);
          onBusy(false);
        } else {
          setRecovery("success");
          setStarted(true);
        }
      },
      scenario === "slow" ? 2600 : 450,
    );
    return () => clearTimeout(timer);
  }, [recovery, scenario]);
  const phaseRef = useRef(null);
  // Phase announcements do not need to reconcile the entire scene mid-flight.
  const setPhase = (text) => {
    if (phaseRef.current) phaseRef.current.textContent = text;
  };
  const systemReduced = useReducedMotion();
  const reduced = forceReduced || systemReduced;
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;

  useGSAP(
    () => {
      if (!started) return;
      const startedAt = performance.now();
      let setupMs = 0;
      const el = root.current;
      const select = (s) => el.querySelector(s);
      const surface = select(".auth-surface");
      const form = select(".auth-content");
      const compose = select(".compose-content");
      const workspace = select(".workspace");
      const rail = select(".rail");
      const sidebar = select(".sidebar");
      const main = select(".main");
      const content = select(".welcome");
      const origin = el.getBoundingClientRect();
      const localRect = (node) => {
        const r = node.getBoundingClientRect();
        return {
          x: r.x - origin.x,
          y: r.y - origin.y,
          width: r.width,
          height: r.height,
        };
      };
      const source = localRect(surface);
      const constrained =
        quality === "low" ||
        (quality === "auto" &&
          ((navigator.hardwareConcurrency || 8) <= 4 ||
            (navigator.deviceMemory || 8) <= 4));
      const particleCount =
        reduced || mode === "flip" ? 0 : constrained ? 48 : 168;
      // Read the destination once, before animation; no layout reads in draw().
      el.dataset.layout = "workspace";
      const destination = localRect(surface);
      el.dataset.layout = "auth";
      const particles = makeParticles(
        source,
        [localRect(rail), localRect(sidebar), localRect(main), destination],
        particleCount,
        ring ? "ring" : "rect",
      );
      const painter = particleCount
        ? createParticlePainter(
            canvas.current,
            particles,
            origin.width,
            origin.height,
            Math.min(devicePixelRatio || 1, constrained ? 0.75 : 1),
          )
        : null;
      const motion = { progress: 0 };
      let raf = 0,
        last = 0,
        frames = [],
        finished = false,
        interrupted = false;
      const seconds = reduced ? 0.16 : duration / 1000;
      const record = (now) => {
        if (last) frames.push(now - last);
        last = now;
        raf = requestAnimationFrame(record);
      };
      raf = requestAnimationFrame(record);
      const draw = () => painter?.draw(motion.progress);
      const finish = () => {
        if (finished) return;
        finished = true;
        cancelAnimationFrame(raf);
        painter?.dispose();
        el.dataset.layout = "workspace";
        gsap.set([workspace, rail, sidebar, main, content, surface, compose], {
          opacity: 1,
          clearProps: "transform,clipPath,willChange",
        });
        gsap.set(form, { opacity: 0, clearProps: "willChange" });
        workspace.inert = false;
        form.inert = true;
        compose.inert = false;
        const sorted = [...frames].sort((a, b) => a - b);
        onResult({
          mode,
          setupMs,
          requested: seconds * 1000,
          elapsed: Math.round(performance.now() - startedAt),
          p95: +(sorted[Math.floor(sorted.length * 0.95)] || 0).toFixed(1),
          slow: frames.filter((n) => n > 33.4).length,
          samples: frames.length,
          count: painter ? particleCount : 0,
          profile: constrained ? "低功耗" : "标准",
          reduced: reducedRef.current,
          interrupted,
        });
        setDone(true);
        setPhase("已稳定 · 动画资源已释放");
        onBusy(false);
        select("textarea").focus({ preventScroll: true });
      };
      gsap.set(workspace, { opacity: 1 });
      gsap.set([rail, sidebar, main, content, compose], { opacity: 0 });
      gsap.set([surface, rail, sidebar, main, content, compose], {
        willChange: "transform,opacity",
      });
      const tl = gsap.timeline({ paused: true, onComplete: finish });
      // All motion lives in a normalized one-second master timeline, scaled once.
      if (reduced) {
        el.dataset.layout = "workspace";
        tl.to(form, { opacity: 0, duration: 0.3 }, 0).to(
          [rail, sidebar, main, content, compose],
          { opacity: 1, duration: 0.7 },
          0.3,
        );
      } else {
        tl.call(() => setPhase("身份已确认"), [], 0)
          .to(form, { opacity: 0, y: -6, duration: 0.15 }, 0.14)
          .call(
            () =>
              setPhase(
                mode !== "flip" ? "边缘解构 · 受控流动" : "共享表面 · 空间变形",
              ),
            [],
            0.2,
          );
        if (mode === "flip") {
          const state = Flip.getState(surface);
          el.dataset.layout = "workspace";
          const flip = Flip.from(state, {
            duration: 0.64,
            ease: "power3.inOut",
            paused: true,
            scale: true,
          });
          tl.add(flip.play(), 0.22);
        } else {
          tl.to(
            surface,
            {
              opacity: 0,
              scale: 0.975,
              duration: 0.34,
              ease: "power1.in",
            },
            0.2,
          )
            .to(
              motion,
              { progress: 1, duration: 0.72, ease: "none", onUpdate: draw },
              0.18,
            )
            .call(
              () => {
                el.dataset.layout = "workspace";
                gsap.set(surface, { scale: 1 });
              },
              [],
              0.56,
            )
            .to(surface, { opacity: 1, duration: 0.24 }, 0.6);
        }
        tl.call(() => setPhase("导航成形"), [], 0.28)
          .fromTo(rail, { x: -10 }, { opacity: 1, x: 0, duration: 0.2 }, 0.28)
          .call(() => setPhase("侧边栏成形"), [], 0.38)
          .fromTo(
            sidebar,
            { x: -14 },
            { opacity: 1, x: 0, duration: 0.21 },
            0.38,
          )
          .call(() => setPhase("主画布出现"), [], 0.48)
          .to(main, { opacity: 1, duration: 0.26 }, 0.48)
          .fromTo(
            content,
            { y: 10 },
            { opacity: 1, y: 0, duration: 0.22 },
            0.66,
          )
          .to(compose, { opacity: 1, duration: 0.18 }, 0.76)
          .call(() => setPhase("归于安静"), [], 0.94);
        tl.to({}, { duration: 0.03 }, 0.97);
      }
      setupMs = Math.round(performance.now() - startedAt);
      tl.timeScale(1 / seconds).play(0);
      // Resizing/backgrounding cannot leave a half-built shell or a runaway loop.
      const settle = () => {
        if (!finished) {
          interrupted = true;
          tl.progress(1);
        }
      };
      const visibility = () => {
        if (document.hidden) settle();
      };
      const query = matchMedia("(prefers-reduced-motion: reduce)");
      const preference = (e) => {
        if (e.matches) {
          reducedRef.current = true;
          settle();
        }
      };
      window.addEventListener("resize", settle);
      document.addEventListener("visibilitychange", visibility);
      query.addEventListener("change", preference);
      return () => {
        cancelAnimationFrame(raf);
        tl.kill();
        painter?.dispose();
        window.removeEventListener("resize", settle);
        document.removeEventListener("visibilitychange", visibility);
        query.removeEventListener("change", preference);
      };
    },
    { scope: root, dependencies: [started], revertOnUpdate: true },
  );

  return (
    <>
      {mode === "recovery" && (
        <div className="recovery-scenarios">
          <label>
            恢复场景{" "}
            <select
              aria-label="恢复场景"
              disabled={recovery === "checking" || started}
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              <option value="success">快速成功</option>
              <option value="slow">慢请求</option>
              <option value="expired">Cookie 过期</option>
              <option value="error">网络失败</option>
            </select>
          </label>
          <span>圆环只表示等待，不显示虚构百分比；过期回到登录。</span>
        </div>
      )}
      <div
        className="scene"
        ref={root}
        data-layout="auth"
        data-origin={ring ? "ring" : "card"}
        data-state={done ? "settled" : started ? "running" : "ready"}
      >
        <div className="scene-grain" />
        <div className="auth-caption">YOUR SPACE TO THINK, MAKE & DO.</div>
        <div className="workspace" inert={!done}>
          <nav className="rail" aria-label="应用导航">
            <b className="brand-icon">
              h<span>·</span>
            </b>
            <span className="rail-active">✳</span>
            <span>▤</span>
            <span>▦</span>
            <span className="rail-bottom">HP</span>
          </nav>
          <aside className="sidebar">
            <div className="side-title">
              工作空间 <span>⌄</span>
            </div>
            <button className="new-chat">＋ 新对话</button>
            <small>最近对话</small>
            <div className="conversation selected">从一个想法开始</div>
            <div className="conversation">产品设计探索</div>
            <div className="conversation">本周工作计划</div>
            <div className="side-footer">
              <span className="avatar">H</span>
              <div>
                我的工作空间<small>Personal workspace</small>
              </div>
            </div>
          </aside>
          <main className="main">
            <header>
              <span>AI / 新对话</span>
              <span>◷　⋯</span>
            </header>
            <div className="welcome">
              <div className="eyebrow">A LITTLE SPACE. A BIG POSSIBILITY.</div>
              <h2 tabIndex={-1}>今天，想一起完成什么？</h2>
              <p>让想法有处安放，让工作自然向前。</p>
              <div className="suggestions">
                <span>梳理一个想法 ↗</span>
                <span>开始一项研究 ↗</span>
                <span>创作一些内容 ↗</span>
              </div>
            </div>
            <footer>HpAgent · 把复杂留给我们</footer>
          </main>
        </div>
        <div className="auth-surface">
          <div className="auth-content" inert={started}>
            {ring ? (
              <div className="recovery-prototype">
                <svg
                  className={
                    recovery === "checking"
                      ? "recovery-ring spinning"
                      : "recovery-ring"
                  }
                  viewBox="0 0 240 240"
                  aria-hidden="true"
                >
                  <circle cx="120" cy="120" r="112" />
                  <circle className="arc" cx="120" cy="120" r="112" />
                </svg>
                <h1>HpAgent</h1>
                <p role={recovery === "error" ? "alert" : "status"}>
                  {recovery === "checking"
                    ? "正在验证会话…"
                    : recovery === "success"
                      ? "会话已恢复 ✓"
                      : recovery === "error"
                        ? "连接失败，请重试"
                        : "恢复你的工作空间"}
                </p>
                {!started && recovery !== "checking" && (
                  <button
                    className="recovery-start"
                    onClick={() => {
                      onBusy(true);
                      setRecovery("checking");
                    }}
                  >
                    {recovery === "error" ? "重试恢复" : "模拟 Cookie 恢复"}
                  </button>
                )}
              </div>
            ) : (
              <>
                <div className="auth-brand">
                  <b className="brand-icon">
                    h<span>·</span>
                  </b>
                  <span>HpAgent</span>
                </div>
                <h1>欢迎回来</h1>
                <p>从这里，进入你的工作空间。</p>
                <label>
                  邮箱
                  <input
                    aria-label="模拟邮箱"
                    value="hello@hpagent.demo"
                    readOnly
                    tabIndex={started ? -1 : 0}
                  />
                </label>
                <label>
                  密码
                  <input
                    aria-label="模拟密码"
                    type="password"
                    value="prototype"
                    readOnly
                    tabIndex={started ? -1 : 0}
                  />
                </label>
                <button
                  className={`login ${started ? "success" : ""}`}
                  onClick={() => {
                    onBusy(true);
                    setPhase("身份已确认");
                    setStarted(true);
                  }}
                  disabled={started}
                >
                  {started ? "身份已确认 ✓" : "模拟登录成功 →"}
                </button>
                <div className="auth-note">无需真实账号 · 仅演示视觉转场</div>
              </>
            )}
          </div>
          <div className="compose-content" inert={!done}>
            <textarea
              aria-label="工作台输入框"
              placeholder="告诉我，你想做什么…"
            />
            <div>
              <span>
                ＋　⌕ <small>添加上下文</small>
              </span>
              <span>
                <small>自动</small>　
                <button aria-label="演示发送按钮" disabled>
                  ↑
                </button>
              </span>
            </div>
          </div>
        </div>
        {started && !done && !reduced && mode !== "flip" && (
          <canvas ref={canvas} aria-hidden="true" className="particles" />
        )}
      </div>
      <div className="stage-status" role="status">
        <span className={started && !done ? "live-dot active" : "live-dot"} />
        <span ref={phaseRef}>等待模拟登录</span>
        <span>
          {reduced
            ? "REDUCED MOTION · 160 ms"
            : `${mode !== "flip" ? "CANVAS + TIMELINE" : "FLIP + TIMELINE"} · ${duration} ms`}
        </span>
      </div>
    </>
  );
}

function App() {
  const [mode, setMode] = useState("particles");
  const [duration, setDuration] = useState(1280);
  const [quality, setQuality] = useState("auto");
  const [forceReduced, setForceReduced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const [results, setResults] = useState([]);
  const systemReduced = useReducedMotion();
  const reset = () => {
    setBusy(false);
    setVersion((n) => n + 1);
  };
  return (
    <div className="lab">
      <header className="lab-header">
        <div className="wordmark">
          HpAgent <span>/ Motion studies</span>
        </div>
        <span className="edition">PROTOTYPE 01 — V1.2</span>
      </header>
      <section className="intro">
        <div>
          <div className="eyebrow">AUTH → WORKSPACE</div>
          <h1>一次进入，两种表达。</h1>
          <p>
            相同起点，相同终点。比较界面如何从身份确认，走向安静的工作空间。
          </p>
        </div>
        <div className="duration-tag">
          900–1600{" "}
          <small>
            ms
            <br />
            CINEMATIC WINDOW
          </small>
        </div>
      </section>
      <div className="controls">
        <div className="mode-switch" aria-label="转场方案">
          {[
            ["particles", "01", "粒子解构"],
            ["flip", "02", "空间变形"],
            ["recovery", "03", "Cookie 恢复"],
          ].map(([id, num, title]) => (
            <button
              key={id}
              aria-pressed={mode === id}
              disabled={busy}
              onClick={() => {
                setMode(id);
                reset();
              }}
            >
              <small>{num}</small> {title}
            </button>
          ))}
        </div>
        <div className="settings">
          <label>
            时长{" "}
            <select
              aria-label="转场时长"
              value={duration}
              disabled={busy}
              onChange={(e) => {
                setDuration(+e.target.value);
                reset();
              }}
            >
              {[900, 1280, 1600].map((n) => (
                <option key={n} value={n}>
                  {n} ms
                </option>
              ))}
            </select>
          </label>
          <label>
            画质{" "}
            <select
              aria-label="粒子预算"
              value={quality}
              disabled={busy}
              onChange={(e) => {
                setQuality(e.target.value);
                reset();
              }}
            >
              <option value="auto">自动</option>
              <option value="low">低功耗</option>
              <option value="standard">标准</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              disabled={busy}
              checked={forceReduced}
              onChange={(e) => {
                setForceReduced(e.target.checked);
                reset();
              }}
            />
            减少动态效果
          </label>
          <button className="replay" onClick={reset}>
            {busy ? "取消 / 重置" : "↻ 重置演示"}
          </button>
        </div>
      </div>
      <Scene
        key={`${mode}-${version}`}
        {...{ mode, duration, quality, forceReduced }}
        onBusy={setBusy}
        onResult={(r) => setResults((prev) => [r, ...prev].slice(0, 8))}
      />
      <section className="notes">
        <div>
          <span className="note-number">
            {mode !== "flip" ? "01 / DISSOLVE" : "02 / RECOMPOSE"}
          </span>
          <h3>
            {mode !== "flip"
              ? "界面化为微粒，再聚为秩序。"
              : "一个表面，延续到下一段工作。"}
          </h3>
          <p>
            {mode !== "flip"
              ? "借鉴 Codrops 的解构语言：中性微粒从卡片边缘沿受控路径流向工作区，极少暖色点缀，完成后完全卸载。"
              : "真实 GSAP Flip 测量同一个表面：登录卡片连续变形为输入区域，工作区骨架依次成形，保留空间记忆。"}
          </p>
        </div>
        <div className="sequence">
          <span>SUCCESS</span>
          <i>→</i>
          <span>{mode !== "flip" ? "DISSOLVE" : "MORPH"}</span>
          <i>→</i>
          <span>ASSEMBLE</span>
          <i>→</i>
          <span>SETTLE</span>
          <p>
            系统动态效果偏好：{systemReduced ? "减少动态效果" : "正常"} ·
            低功耗模式限制粒子数量与像素密度，不等同于真实设备测试。
          </p>
        </div>
      </section>
      <section className="measurements">
        <div className="measure-title">
          <h3>运行记录</h3>
          <span>当前浏览器实测 · 帧间隔包含浏览器调度开销</span>
        </div>
        {results.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "方案",
                    "目标 / 实际",
                    "P95 帧间隔",
                    ">33.4ms 帧",
                    "粒子 / 预算",
                    "结果",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} data-setup-ms={r.setupMs}>
                    <td>{r.mode !== "flip" ? "粒子解构" : "空间变形"}</td>
                    <td>
                      <span title={`含准备开销 ${r.setupMs} ms`}>
                        {r.requested} / {r.elapsed} ms
                      </span>
                    </td>
                    <td>{r.p95} ms</td>
                    <td>
                      {r.slow} / {r.samples}
                    </td>
                    <td>
                      {r.count} / {r.profile}
                    </td>
                    <td>
                      {r.interrupted
                        ? "中断后直接稳定"
                        : r.reduced
                          ? "无粒子快速切换"
                          : r.elapsed >= 900 && r.elapsed <= 1600
                            ? "时长在目标区间"
                            : "需复查时长 / 中断"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">点击「模拟登录成功」生成本次运行数据。</p>
        )}
      </section>
      <footer className="lab-footer">
        独立技术验证 · 模拟身份确认 · 未连接认证服务
        <span>HpAgent / Interface Matter Recomposition</span>
      </footer>
    </div>
  );
}
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
