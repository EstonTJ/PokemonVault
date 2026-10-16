{ pkgs }: {
  deps = [
    pkgs.ffmpeg-headless
    pkgs.libGL
    pkgs.glib
  ];
}
