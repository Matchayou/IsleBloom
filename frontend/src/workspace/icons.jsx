import React from 'react';
import {
  FolderIcon, ChatIcon, SearchIcon, FileIcon, GridIcon, BookIcon,
  OwlIcon, PlusIcon, ChevronRightIcon, CloseIcon, ArrowUpRightIcon,
  ArrowLeftIcon, SendIcon, MenuIcon, MaximizeIcon, CheckIcon,
  LeafIcon, FlowerIcon, SunIcon, TreeIcon, EllipsisIcon, ZoomInIcon, ZoomOutIcon,
} from 'naive-icons';

const icons = {
  folder: FolderIcon, chat: ChatIcon, search: SearchIcon, file: FileIcon,
  chart: BookIcon, bot: OwlIcon, grid: GridIcon, layers: BookIcon,
  plus: PlusIcon, chevron: ChevronRightIcon, close: CloseIcon,
  arrow: ArrowUpRightIcon, back: ArrowLeftIcon, send: SendIcon,
  panel: MenuIcon, expand: MaximizeIcon, check: CheckIcon, logo: LeafIcon,
  flower: FlowerIcon, sun: SunIcon, tree: TreeIcon, more: EllipsisIcon,
  zoomIn: ZoomInIcon, zoomOut: ZoomOutIcon,
};

export function Icon({name, size=18, ...props}) {
  const Component = icons[name];
  return <Component size={size} color="currentColor" aria-hidden="true" {...props}/>;
}
