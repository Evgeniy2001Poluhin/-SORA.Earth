import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import * as THREE from "three";
import "./home.css";
export function HomePage() {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, el.clientWidth/el.clientHeight, 0.1, 100);
    camera.position.set(0,0,3.6);
    const renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true });
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    el.appendChild(renderer.domElement);
    const globe = new THREE.Mesh(new THREE.SphereGeometry(1,64,64),
      new THREE.MeshStandardMaterial({ color:0x0b1720, roughness:.95, metalness:.1 }));
    scene.add(globe);
    const grid = new THREE.LineBasicMaterial({ color:0x2FE0A6, transparent:true, opacity:.22 });
    for (let lat=-80; lat<=80; lat+=20) {
      const phi=(90-lat)*Math.PI/180, r=Math.sin(phi), y=Math.cos(phi);
      const pts:THREE.Vector3[]=[]; for(let i=0;i<=64;i++){const t=(i/64)*Math.PI*2;
        pts.push(new THREE.Vector3(r*Math.cos(t),y,r*Math.sin(t)));}
      globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), grid));
    }
    for (let lon=0; lon<360; lon+=20) {
      const th=lon*Math.PI/180; const pts:THREE.Vector3[]=[];
      for(let i=0;i<=64;i++){const phi=(i/64)*Math.PI;
        pts.push(new THREE.Vector3(Math.sin(phi)*Math.cos(th),Math.cos(phi),Math.sin(phi)*Math.sin(th)));}
      globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), grid));
    }
    const glow = new THREE.Mesh(new THREE.SphereGeometry(1.22,64,64),
      new THREE.ShaderMaterial({ transparent:true, side:THREE.BackSide, blending:THREE.AdditiveBlending,
        vertexShader:`varying vec3 vN; void main(){vN=normalize(normalMatrix*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
        fragmentShader:`varying vec3 vN; void main(){ float i=pow(.68 - dot(vN, vec3(0,0,1.)), 2.); gl_FragColor=vec4(.18,.88,.65, i);}`}));
    scene.add(glow);
    [{lat:59.3,lon:18.0,c:0x2FE0A6},{lat:52.5,lon:13.4,c:0x2FE0A6},{lat:48.8,lon:2.3,c:0x4FA7FF},
     {lat:40.4,lon:-3.7,c:0xF1C159},{lat:35.6,lon:139.6,c:0x4FA7FF},{lat:-33.8,lon:151.2,c:0x2FE0A6},
     {lat:40.7,lon:-74,c:0xFF8A6E}].forEach(p=>{
      const phi=(90-p.lat)*Math.PI/180, th=(p.lon+180)*Math.PI/180;
      const x=-Math.sin(phi)*Math.cos(th), z=Math.sin(phi)*Math.sin(th), y=Math.cos(phi);
      const pin=new THREE.Mesh(new THREE.SphereGeometry(.014,16,16),new THREE.MeshBasicMaterial({color:p.c}));
      pin.position.set(x*1.01,y*1.01,z*1.01); globe.add(pin);
    });
    scene.add(new THREE.AmbientLight(0xffffff,.35));
    const dir=new THREE.DirectionalLight(0x9fe4cd,1.1); dir.position.set(5,3,5); scene.add(dir);
    let raf=0; const tick=()=>{globe.rotation.y+=0.0018; renderer.render(scene,camera); raf=requestAnimationFrame(tick);}; tick();
    const onResize=()=>{const w=el.clientWidth,h=el.clientHeight; camera.aspect=w/h; camera.updateProjectionMatrix(); renderer.setSize(w,h);};
    window.addEventListener("resize",onResize);
    return ()=>{cancelAnimationFrame(raf); window.removeEventListener("resize",onResize); renderer.dispose(); if(renderer.domElement.parentNode===el) el.removeChild(renderer.domElement);};
  }, []);
  return (
    <div className="home">
      <section className="home-hero">
        <div>
          <motion.div className="eyebrow" initial={{opacity:0,y:10}} animate={{opacity:1,y:0}} style={{display:"inline-flex",alignItems:"center",gap:10,color:"var(--planet)",marginBottom:20}}>
            <span style={{width:6,height:6,borderRadius:"50%",background:"var(--planet)",boxShadow:"0 0 0 4px rgba(47,224,166,.18)"}}/>
            ESG · CO₂ · ML · SHAP
          </motion.div>
          <motion.h1 className="display" initial={{opacity:0,y:14}} animate={{opacity:1,y:0}} transition={{delay:.05,duration:.6,ease:[.16,1,.3,1]}}
            style={{fontSize:"clamp(40px,5.6vw,76px)",lineHeight:.98}}>
            Planetary-scale <em style={{fontStyle:"italic",color:"var(--planet)"}}>ESG</em> intelligence,<br/>explainable down to the feature.
          </motion.h1>
          <motion.p initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} transition={{delay:.15,duration:.6,ease:[.16,1,.3,1]}}
            style={{color:"var(--muted)",maxWidth:"58ch",fontSize:16,lineHeight:1.6}}>
            Score sustainability projects across 32 countries with a closed-loop MLOps pipeline: drift detection, automated retraining, SHAP explanations and AI Teammate oversight.
          </motion.p>
          <motion.div initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} transition={{delay:.25,duration:.6,ease:[.16,1,.3,1]}}
            style={{display:"flex",gap:12,marginTop:26}}>
            <Link to="/evaluate" className="btn-primary">Run evaluation</Link>
            <a href="/docs" target="_blank" rel="noreferrer" className="btn-ghost">API docs →</a>
          </motion.div>
        </div>
        <div className="home-globe" ref={host}/>
      </section>
      <section className="home-kpis">
        <Kpi k="Models" v="4" sub="RF · XGB · MLP · Stacking"/>
        <Kpi k="Production AUC" v="0.82" sub="CV 0.98 · calibrated"/>
        <Kpi k="MLflow runs" v="100" sub="tracked · artifacts · SHAP"/>
        <Kpi k="API endpoints" v="42+" sub="FastAPI · /api/v1"/>
        <Kpi k="Services" v="8" sub="Docker Compose"/>
        <Kpi k="Tests passed" v="324" sub="0 failed"/>
      </section>
      <section className="home-how">
        <div className="eyebrow">HOW IT WORKS</div>
        <div className="how-grid">
          <div className="how-step"><span className="how-num">01</span><h3>Submit</h3><p>Project name, country, budget, CO₂, social score, duration. Six fields, no spreadsheets.</p></div>
          <div className="how-step"><span className="how-num">02</span><h3>Score</h3><p>Stacked ML model returns ESG total + env/soc/eco breakdown, success probability and risk level.</p></div>
          <div className="how-step"><span className="how-num">03</span><h3>Compare</h3><p>Run two projects side-by-side. See the gap on every axis. Decide with nrs, not narratives.</p></div>
        </div>
      </section>
      <section className="home-cta">
        <div>
          <h2 className="display" style={{fontSize:"clamp(28px,3.6vw,44px)",margin:0}}>Ready to score your project?</h2>
          <p style={{color:"var(--muted)",marginTop:8}}>Same backend that powers the live tile above. ~300ms per evaluation.</p>
        </div>
        <div style={{display:"flex",gap:12}}>
          <Link to="/evaluate" className="btn-primary">Run evaluation</Link>
          <Link to="/compare" className="btn-ghost">Compare two →</Link>
        </div>
      </section>
    </div>
  );
}
function Kpi({k,v,sub}:{k:string;v:string;sub:string}) {
  return (<div className="kpi card">
    <div className="eyebrow" style={{marginBottom:10}}>{k}</div>
    <div className="display tabular" style={{fontSize:36,lineHeight:1,marginBottom:6}}>{v}</div>
    <div style={{color:"var(--muted)",fontSize:12.5}}>{sub}</div>
  </div>);
}
