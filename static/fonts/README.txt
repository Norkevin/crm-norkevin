Para que el PDF use exactamente la misma tipografia que la web,
dejar estos tres archivos en static/fonts/:
  Inter-Regular.ttf
  Inter-Medium.ttf
  Inter-SemiBold.ttf
Se descargan de https://fonts.google.com/specimen/Inter (Get font ->
Download all) o de https://github.com/rsms/inter/releases.
No hay que tocar codigo: si los archivos estan, se registran solos;
si no, el PDF usa Helvetica, que tambien es sans y se ve limpia.
