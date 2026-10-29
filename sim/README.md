# sim/

Mỗi subproject một thư mục con, tên trùng `subprojects[].name` trong project.yaml.

```
sim/
  MAC/            <- ví dụ subproject MAC
    CMakeLists.txt, main.cc, src/, include/      (Geant4)
    run_S1_0p5cm_59p5keV.mac ...                 (file input, tên theo input_template)
  MSP/
    ...
```

- Tên file input phải sinh được từ `input_template` (biến {sample} {thickness} {energy} dùng chữ p cho dấu thập phân).
- Muốn số hạt lấy từ project.yaml: viết `@PRIMARIES@` trong file input (vd `/run/beamOn @PRIMARIES@`).
- Không để build/, file kết quả, file .root, .csv kết quả trong sim/.
