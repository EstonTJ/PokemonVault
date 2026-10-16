try { if (localStorage.getItem("pv-theme") !== "light") document.documentElement.classList.add("dark"); }
catch (e) { document.documentElement.classList.add("dark"); }
