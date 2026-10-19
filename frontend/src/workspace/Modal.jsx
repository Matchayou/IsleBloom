import {Button, Modal as IslandModal} from 'animal-island-ui';
import React from 'react';

export default function Modal({title, onClose, children, width="min(680px, calc(100vw - 24px))"}) {
  return <IslandModal open variant="game" title={title} onClose={onClose}
    typewriter={false} width={width}
    footer={<Button onClick={onClose}>关闭</Button>}>
    <div className="dialog-content">{children}</div>
  </IslandModal>;
}
