# video - durata 04:46 - token stimati ~2539
DemoBrief 0.7.4. Fonti usate: voce (851 parole), moto (43), schermate (15).
Legenda: [mm:ss] istante; VOCE = parlato; TESTO = scritte che cambiano a schermo (OCR); SCHERMO = righe nuove lette quando lo schermo si ferma su una vista nuova (dove: pagina, finestra, pannello); MOTO = azioni (mesh Ng = gesti sulla geometria, orbita Ns = rotazione vista, clicN = clic, taglio vista = cambio scena); VISTA = geometria nuova vista dall'occhio (forma, zona). Immagini a richiesta: fotogrammi/indice.md.
[00:00] VOCE "The Extrude tool is one of the fundamental tools for modeling." | MOTO 00:00 taglio vista
[00:06] VOCE "Well, allow me to simply show you." | SCHERMO 00:08 pagina: File Edit Render Window Help · Layout Modeling Sculpting UVEditing Texture Paint Shading Animation Rendering Co · Scene · View Layer / Edit Mode · View Select Add Mesh Vertex Edge Face UV Global / Collection / Camera / Vcube / Add Modifier / Select Box Select · Cube|Verts:4/8|Edg · Tris:12|Mem: 48.6MB|v2.80.74 | MOTO 00:07-00:10 clic3
[00:08] VOCE "If you select a face and click the Extrude tool in the Quick Tools menu, you'll see this yellow gizmo. This is a normal axis manipulator."
[00:17] VOCE "what does that mean?" | SCHERMO 00:19 finestra al centro: Extrude region together along the average normal / Shortcut:Tweak Left | MOTO 00:17 clic1
[00:19] VOCE "Well, it certainly doesn't look normal, and that's because normals are a type of vector related to faces." | MOTO 00:19 clic1
[00:25] VOCE "Simply put, the normal is the vector that sticks out perpendicular to the surface of a face." | MOTO 00:26 clic2
[00:31] VOCE "If we go ahead and left -click -drag this manipulator, you'll notice that it duplicates the face and moves it along the normal axis." | MOTO 00:32 clic2
[00:38] VOCE "it also creates new edges and faces between the original face you had selected and the new duplicate face to maintain a connection." | MOTO 00:41 orbita 1s; 00:44 orbita 2s
[00:48] VOCE "You can also left -click -drag anywhere else in the viewport instead to move the extrusion along your viewing plane." | MOTO 00:51-00:57 mesh 1g
[00:55] VOCE "You can do this for all types of selections, including multiple faces, vertices, and edges."
[01:01] VOCE "However, if you select multiple faces that point in different directions," | MOTO 01:01-01:04 orbita 4s clic1
[01:06] VOCE "you'll notice that your manipulator is pointing at sort of an average value of where all your selected faces are facing." | MOTO 01:07-01:10 orbita 1s clic1
[01:13] VOCE "Keep this in mind, as we'll come back to it later." | MOTO 01:13-01:16 orbita 2s
[01:15] VOCE "It's important to note that if you right -click while dragging the manipulator, you can cancel the extrude, but it will not remove the duplicate faces." | MOTO 01:18 orbita 1s clic1; 01:22 clic1
[01:23] VOCE "This is intentional, as it allows you to create new geometry at the same location as the originally selected geometry."
[01:30] VOCE "This can be helpful when wanting to scale or rotate your extruded mesh." | SCHERMO 01:33 pagina: lect Box Select · Cube|Verts:8/9|Edges:12/12F
[01:35] VOCE "For example, what if we wanted to create a stack of connected cubes?" | MOTO 01:38 clic1
[01:39] VOCE "You could simply extrude, right -click cancel, and press S to scale your extrusion down." | MOTO 01:41 clic3; 01:45 clic1
[01:45] VOCE "This will then let you extrude again and confirm to create the new smaller box." | SCHERMO 01:47 pagina: Cube|Verts:4/16|Edges:4/28|Fa; 01:49 pagina: Edit Mode · View Select Add Mesh Vertex Edge Face UV Global
[01:50] VOCE "This was made super easy by the fact that the extrusion cancel brings the extruded geometry down back to the original cube surface." | MOTO 01:55-02:00 clic3; 01:55 clic1
[01:59] VOCE "However, if you do this on accident and you want to remove the extrusion, just make sure you press Ctrl+Z to undo it properly." | MOTO 02:02 clic1; 02:04 clic1; 02:04 clic1
[02:07] VOCE "For hotkey users, simply press E as in extrude to begin moving the extruded selection along its normal vector." | SCHERMO 02:07 pagina: Cube|Verts:4/24 | MOTO 02:11 clic2
[02:14] VOCE "Right -click will do the same thing I mentioned before, and left -click will confirm your extrusion."
[02:19] VOCE "the Extrude tool has a few more tricks up its sleeve." | SCHERMO 02:21 pannello a sinistra: Box Select | MOTO 02:19 clic1
[02:23] VOCE "If you click and drag the Extrude tool icon in the Quick Tools menu, it will open up a submenu with more extrude options." | SCHERMO 02:26 pannello a sinistra: Extrude freely or along an axis / Shortcut: Shift Spacebar,E | MOTO 02:25-02:32 clic11
[02:30] VOCE "The first option is our default option, so we'll skip that."
[02:34] VOCE "our next option is called Extrude Along Normals." | MOTO 02:34 clic1
[02:37] VOCE "This is what you might prefer to use if you select multiple faces that point in different directions." | MOTO 02:38-02:41 clic3
[02:43] VOCE "This will allow you to sort of push the geometry outwards along each individual face's normals, but still keep the faces connected to each other after extrusion." | MOTO 02:47 clic1
[02:53] VOCE "This is great for adding a layer of thickness to your mesh, for example." | SCHERMO 02:53 pagina: Cube|Verts:7/14|Edges:9/24|Fa | MOTO 02:56-03:00 mesh 1g clic2
[02:57] VOCE "The next Extrude subtool is the Extrude Individual tool."
[03:01] VOCE "This is extremely helpful for making certain faces extend out independently from its neighbors." | SCHERMO 03:02 pagina: ect Extrude Individual Faces and Move · Cube |Verts:8/8|Edges:12/12 | MOTO 03:04 clic2
[03:07] VOCE "For example, you could use this to create spikes or stylized bumps on a surface." | MOTO 03:08-03:15 mesh 2g clic4
[03:12] VOCE "Quick tip, this tool works best when faces are selected, not floating vertices or edges."
[03:19] VOCE "The final Extrude tool is powerful and easy to use." | MOTO 03:21 clic1
[03:22] VOCE "It's called the Extrude to Cursor tool and allows you to extrude your selection and extend it to the location of wherever you left -click in the viewport." | MOTO 03:25 clic2; 03:29-03:33 clic6
[03:31] VOCE "This will still be along your viewing plane, but allows you to work roughly and quickly when you need to."
[03:37] VOCE "For hot key users, if you're in edit mode, you can access the Extrude to Cursor tool at any time by simply holding Ctrl and right -clicking." | MOTO 03:36 mesh 1g; 03:43-03:47 mesh 1g clic2
[03:46] VOCE "that we've learned about the Extrude tool, I'm going to go ahead and show you how to make that chair I made earlier." | SCHERMO 03:51 pagina: Pan View · Cube|Verts:24/56|Edges:26/108|Fa
[03:53] VOCE "This is what we want to make, and here's how we do it with the Extrude tool." | MOTO 03:56 mesh 1g
[03:57] VOCE "Simply select your cube, go into edit mode, and scale the cube down along the z -axis." | SCHERMO 03:58 pagina: Global / Select Box Select · Collection|Cube|Verts:8|Faces:6|Tri | MOTO 04:00 clic2
[04:03] VOCE "We will then use the Loop Cut tool, which will be covered in its own video, to make some additional cuts on our cube." | SCHERMO 04:09 pagina: Edit Mode / Cube|Verts:8/8|Edges:12/12
[04:11] VOCE "To use the Loop Cut tool, simply select it on the Quick Tools menu and left -click -drag to create a tic -tac -toe pattern like so." | SCHERMO 04:19 pannello a sinistra: Edge Slide:-0.0172 (E)ven:OFF,Alt or (C lamp:ON | MOTO 04:13 clic2
[04:19] VOCE "This will then allow us to use our Extrude tool."
[04:22] VOCE "Using Face Select mode, select the faces you would want to use to form the back of the chair as well as the legs of the chair." | MOTO 04:24 orbita 1s clic1; 04:28 clic1
[04:30] VOCE "You can then go to the Extrude tool and select the subtool, Extrude Along Normals." | SCHERMO 04:35 pagina: Cube|Verts:24/32 Edges:26/60 | MOTO 04:31-04:35 mesh 2g clic1
[04:36] VOCE "Then simply left -click -drag to bring out both the chair back and the chair legs at the same time." | MOTO 04:39-04:44 mesh 1g orbita 1s clic2
[04:43] VOCE "Ta -da! You've officially modeled something in Blender."
