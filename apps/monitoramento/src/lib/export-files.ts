type WritableFile = { createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }> };
type Folder = { name: string; getFileHandle(name: string, options: { create: boolean }): Promise<WritableFile> };
type PickerWindow = Window & { showDirectoryPicker?: (options: { mode: string }) => Promise<Folder>; showSaveFilePicker?: (options: { suggestedName: string }) => Promise<WritableFile> };
let folder: Folder | undefined;
export async function chooseExportFolder() {
  const picker = (window as PickerWindow).showDirectoryPicker;
  if (!picker) throw new Error('Este navegador não permite escolher uma pasta. Use Chrome ou Edge, com HTTPS ou localhost.');
  folder = await picker({ mode: 'readwrite' });
  return folder.name;
}
export async function saveExport(name: string, blob: Blob): Promise<'saved' | 'downloaded'> {
  const picker = (window as PickerWindow).showSaveFilePicker;
  if (folder || picker) {
    const file = folder ? await folder.getFileHandle(name, { create: true }) : await picker?.({ suggestedName: name });
    if (!file) throw new Error('Escolha uma pasta para salvar.');
    const writable = await file.createWritable();
    await writable.write(blob); await writable.close();
    return 'saved';
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return 'downloaded';
}