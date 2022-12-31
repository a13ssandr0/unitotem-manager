package org.unitotem.webview;
// Copyright (c) 2014 The Chromium Embedded Framework Authors. All rights
// reserved. Use of this source code is governed by a BSD-style license that
// can be found in the LICENSE file.

import me.friwi.jcefmaven.*;
import org.cef.CefApp;
import org.cef.CefApp.CefAppState;
import org.cef.CefClient;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefMessageRouter;

import javax.swing.*;
import java.awt.*;
import java.awt.event.*;
import java.io.IOException;

public class WebView extends JFrame {
    public static void main(String[] args) throws UnsupportedPlatformException, CefInitializationException, IOException, InterruptedException {
        CefAppBuilder builder = new CefAppBuilder();
        builder.getCefSettings().windowless_rendering_enabled = false;
        // USE builder.setAppHandler INSTEAD OF CefApp.addAppHandler!
        // Fixes compatibility issues with MacOSX
        builder.setAppHandler(new MavenCefAppHandlerAdapter() {
            @Override
            public void stateHasChanged(org.cef.CefApp.CefAppState state) {
                // Shutdown the app if the native CEF part is terminated
                if (state == CefAppState.TERMINATED) System.exit(0);
            }
        });

        if (args.length > 0) builder.addJcefArgs(args);

        CefClient client_ = builder.build().createClient();
        client_.addMessageRouter(CefMessageRouter.create()); // (3) Create a simple message router to receive messages from CEF.
        CefBrowser browser = client_.createBrowser("https://campolino.duckdns.org/st.mp4", false, false);

        JFrame frame = new JFrame();
        frame.setUndecorated(true);
        frame.setVisible(true);
        frame.getContentPane().add(browser.getUIComponent(), BorderLayout.CENTER);
        frame.pack();
        frame.setBounds(GraphicsEnvironment.getLocalGraphicsEnvironment().getScreenDevices()[1].getDefaultConfiguration().getBounds());
        frame.addWindowListener(new WindowAdapter() {
            @Override
            public void windowClosing(WindowEvent e) { // (7) To take care of shutting down CEF accordingly, it's important to call
                CefApp.getInstance().dispose();        //     the method "dispose()" of the CefApp instance if the Java
                frame.dispose();                       //     application will be closed. Otherwise you'll get asserts from CEF.
            }
        });
    }
}