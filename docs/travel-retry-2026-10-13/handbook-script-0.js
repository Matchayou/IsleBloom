
  const inputs=document.querySelectorAll('.cost');
  function calc(){
    let sum=0;
    inputs.forEach(i=>sum+=Number(i.value)||0);
    document.getElementById('totalActual').textContent=sum;
    document.getElementById('perPerson').textContent=(sum/2).toFixed(0);
  }
  inputs.forEach(i=>i.addEventListener('input',calc));
