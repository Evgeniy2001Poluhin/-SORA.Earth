import { Link } from "react-router-dom";

export function HeroVideo() {
  return (
    <section style={{
      position: "relative",
      width: "100vw",
      minHeight: "100vh", marginTop: "-94px",
      marginLeft: "calc(50% - 50vw)",
      marginRight: "calc(50% - 50vw)",
      marginBottom: "0",
      overflow: "hidden",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "#080c0a",
    }}>
      <video autoPlay loop muted playsInline
        style={{position:"absolute",top:0,left:0,width:"100%",height:"100%",objectFit:"cover",zIndex:0}}>
        <source src="/static/spa/hero.mp4" type="video/mp4" />
      </video>

      <div style={{position:"absolute",inset:0,
        background:"radial-gradient(ellipse at center, rgba(0,0,0,0.15) 0%, rgba(0,0,0,0.45) 100%)",
        zIndex:1}}/>

      <div style={{position:"absolute",bottom:0,left:0,right:0,height:"380px",
        background:"linear-gradient(180deg, transparent 0%, rgba(8,12,10,0.55) 35%, rgba(8,12,10,0.95) 75%, rgb(8,12,10) 100%)",
        zIndex:1,pointerEvents:"none"}}/>

      <div style={{position:"relative",zIndex:2,textAlign:"center",padding:"2rem 1.5rem"}}>
        <h1 style={{fontSize:"clamp(3rem,8vw,6rem)",fontWeight:700,color:"#fff",
          letterSpacing:"-0.03em",margin:0,textShadow:"0 4px 32px rgba(0,0,0,0.7)"}}>
          SORA<span style={{color:"#34d399"}}>.earth</span>
        </h1>
        <p style={{fontSize:"clamp(1rem,2vw,1.4rem)",color:"#d1fae5",margin:"1rem auto 2rem",
          maxWidth:"640px",textShadow:"0 2px 12px rgba(0,0,0,0.6)"}}>
          Trusted AI for ESG impact assessment
        </p>
        <div style={{display:"flex",gap:"1rem",flexWrap:"wrap",justifyContent:"center"}}>
          <Link to="/evaluate" style={{padding:"0.9rem 2rem",background:"#10b981",color:"#0a0f0c",
            fontWeight:600,borderRadius:"0.6rem",textDecoration:"none",
            boxShadow:"0 8px 24px rgba(16,185,129,0.45)"}}>
            Evaluate Project →
          </Link>
          <Link to="/calibration" style={{padding:"0.9rem 2rem",
            border:"1px solid rgba(255,255,255,0.35)",color:"#fff",borderRadius:"0.6rem",
            textDecoration:"none",background:"var(--line)"}}>
            Explore Platform
          </Link>
        </div>
      </div>

      <div onClick={()=>window.scrollBy({top:window.innerHeight*0.85,behavior:"smooth"})}
        style={{position:"absolute",bottom:"1.5rem",left:"50%",transform:"translateX(-50%)",
        zIndex:3,cursor:"pointer",display:"flex",flexDirection:"column",
        alignItems:"center",gap:"6px",opacity:0.85,minWidth:"160px"}}>
        <span style={{fontSize:"0.7rem",letterSpacing:"0.3em",color:"#fff",whiteSpace:"nowrap"}}>SCROLL</span>
        <div style={{width:"1px",height:"28px",
          background:"linear-gradient(180deg, rgba(52,211,153,0.9), transparent)"}}/>
      </div>
    </section>
  );
}
