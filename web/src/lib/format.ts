export const fmtNum = (n:number,d=1) => n.toLocaleString("en-US",{maximumFractionDigits:d});
export const fmtMoney = (n:number) => "$" + n.toLocaleString("en-US");
export const clamp = (n:number,min=0,max=100) => Math.max(min, Math.min(max,n));
