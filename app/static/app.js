// Подтверждение опасных действий и подстановка почты наставника.
document.addEventListener("submit", function (event) {
  var text = event.target.getAttribute("data-confirm");
  if (text && !window.confirm(text)) event.preventDefault();
});
document.addEventListener("change", function (event) {
  if (event.target.id !== "mentor_id") return;
  var option = event.target.options[event.target.selectedIndex];
  var mail = document.getElementById("mentor_email");
  if (mail) mail.value = option.getAttribute("data-email") || "";
});
