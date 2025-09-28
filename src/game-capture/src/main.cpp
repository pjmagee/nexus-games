// Windows Graphics Capture (WinRT) specific window capture for Heroes of the Storm
// Loop:
//  1. Find Heroes process + main window
//  2. Create WinRT GraphicsCaptureItem for HWND
//  3. Capture frames via Direct3D11CaptureFramePool (free-threaded)
//  4. Throttle to 1 FPS, saving BMPs to sessions/current/frames using atomic .pending -> final rename
//  5. If window or process ends, restart polling

#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <ctime>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <tlhelp32.h>
#include <vector>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <windows.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/base.h>
#include <wrl/client.h>

struct __declspec(uuid("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1")) IDirect3DDxgiInterfaceAccess : IUnknown
{
    virtual HRESULT __stdcall GetInterface(REFIID iid, void** object) = 0;
};

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "windowsapp.lib")

using Microsoft::WRL::ComPtr;
namespace WGC = winrt::Windows::Graphics::Capture;
namespace WGD = winrt::Windows::Graphics::DirectX;
namespace WGD3D11 = winrt::Windows::Graphics::DirectX::Direct3D11;

static const wchar_t* kPrimaryProcessName = L"HeroesOfTheStorm_x64.exe";
static const wchar_t* kAltProcessName = L"HeroesOfTheStorm.exe";  // fallback if x64 suffix differs

static void log_line(const char* msg)
{
    static std::filesystem::path logPath;

    if (logPath.empty())
    {
        // Use same base directory logic as frames_dir
        const char* base_dir = std::getenv("NEXUS_BASE_DIR");
        std::filesystem::path base_path = base_dir ? std::filesystem::path(base_dir) : std::filesystem::current_path();

        logPath = base_path / "sessions" / "current" / "capture.log";
        std::error_code ec;
        std::filesystem::create_directories(logPath.parent_path(), ec);
    }

    SYSTEMTIME st;
    GetSystemTime(&st);

    char line[1024];

    _snprintf_s(line, _TRUNCATE, "%04d-%02d-%02dT%02d:%02d:%02dZ %s\n", st.wYear, st.wMonth, st.wDay, st.wHour,
                st.wMinute, st.wSecond, msg);

    FILE* f = fopen(logPath.string().c_str(), "a");

    if (f)
    {
        fputs(line, f);
        fclose(f);
    }

    // Mirror to debugger & stderr for visibility if file fails
    OutputDebugStringA(line);

    fputs(line, stderr);
}

static void logf(const char* fmt, ...)
{
    char buf[768];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(buf, _TRUNCATE, fmt, ap);
    va_end(ap);
    log_line(buf);
}

static void log_path(const char* label, const std::filesystem::path& p)
{
    // Avoid char8_t conversion issues on MSVC prior to full char8_t interop.
    std::string s = p.string();
    logf("%s=%s", label, s.c_str());
}

static bool find_process(DWORD& pid)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);

    if (snap == INVALID_HANDLE_VALUE)
        return false;

    PROCESSENTRY32W pe{sizeof(pe)};

    if (!Process32FirstW(snap, &pe))
    {
        CloseHandle(snap);
        return false;
    }
    do
    {
        if (_wcsicmp(pe.szExeFile, kPrimaryProcessName) == 0 || _wcsicmp(pe.szExeFile, kAltProcessName) == 0)
        {
            pid = pe.th32ProcessID;
            CloseHandle(snap);
            return true;
        }
    } while (Process32NextW(snap, &pe));

    CloseHandle(snap);

    return false;
}

static HWND find_main_hwnd(DWORD pid)
{
    struct Ctx
    {
        DWORD pid;
        HWND hwnd;
    }

    ctx{pid, nullptr};

    EnumWindows(
        [](HWND h, LPARAM lp) -> BOOL
        {
            auto* c = (Ctx*)lp;
            DWORD wpid = 0;
            GetWindowThreadProcessId(h, &wpid);
            if (wpid == c->pid && GetWindow(h, GW_OWNER) == NULL && IsWindowVisible(h))
            {
                c->hwnd = h;
                return FALSE;
            }
            return TRUE;
        },
        (LPARAM)&ctx);

    return ctx.hwnd;
}

static HWND find_window_by_title_substring(const wchar_t* needleLower)
{
    struct Ctx
    {
        const wchar_t* needle;
        HWND hwnd;
    }

    ctx{needleLower, nullptr};

    EnumWindows(
        [](HWND h, LPARAM lp) -> BOOL
        {
            auto* c = (Ctx*)lp;

            if (!IsWindowVisible(h))
                return TRUE;

            wchar_t title[512];

            if (!GetWindowTextW(h, title, 512))
                return TRUE;

            std::wstring t = title;

            for (auto& ch : t)
                ch = (wchar_t)towlower(ch);

            std::wstring n = c->needle;

            if (t.find(n) != std::wstring::npos)
            {
                c->hwnd = h;
                return FALSE;
            }
            return TRUE;
        },
        (LPARAM)&ctx);

    return ctx.hwnd;
}
struct BmpWriter
{
    // Input buffer expected BGRA (B,G,R,A). Converts to 24-bit BGR.
    static bool write(const std::filesystem::path& p, const unsigned char* bgra, int w, int h)
    {
        BITMAPFILEHEADER fh{};
        BITMAPINFOHEADER ih{};
        ih.biSize = sizeof(ih);
        ih.biWidth = w;
        ih.biHeight = -h;
        ih.biPlanes = 1;
        ih.biBitCount = 24;
        ih.biCompression = BI_RGB;
        int stride = w * 3;
        int pad = (4 - (stride % 4)) & 3;
        int dataSize = (stride + pad) * h;
        fh.bfType = 0x4D42;
        fh.bfOffBits = sizeof(fh) + sizeof(ih);
        fh.bfSize = fh.bfOffBits + dataSize;

        FILE* f = _wfopen(p.wstring().c_str(), L"wb");

        if (!f)
            return false;

        fwrite(&fh, sizeof(fh), 1, f);
        fwrite(&ih, sizeof(ih), 1, f);

        std::vector<unsigned char> row(stride + pad);

        for (int y = 0; y < h; ++y)
        {
            const unsigned char* src = &bgra[y * w * 4];
            for (int x = 0; x < w; ++x)
            {  // BGR ordering in file
                unsigned char B = src[x * 4 + 0];
                unsigned char G = src[x * 4 + 1];
                unsigned char R = src[x * 4 + 2];
                row[x * 3 + 0] = B;
                row[x * 3 + 1] = G;
                row[x * 3 + 2] = R;
            }

            if (pad)
                memset(row.data() + stride, 0, pad);

            fwrite(row.data(), 1, stride + pad, f);
        }

        fclose(f);

        return true;
    }
};

static std::filesystem::path frames_dir()
{
    // Check for NEXUS_BASE_DIR environment variable, default to current working directory
    const char* base_dir = std::getenv("NEXUS_BASE_DIR");

    std::filesystem::path base_path = base_dir ? std::filesystem::path(base_dir) : std::filesystem::current_path();
    std::filesystem::path p = base_path / "sessions" / "current" / "frames";
    std::error_code ec;
    std::filesystem::create_directories(p, ec);
    return p;
}

static WGD3D11::IDirect3DDevice to_direct3d_device(ID3D11Device* d3dDevice)
{
    winrt::com_ptr<IDXGIDevice> dxgiDevice;
    d3dDevice->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void**>(dxgiDevice.put()));
    winrt::com_ptr<IInspectable> insp;
    CreateDirect3D11DeviceFromDXGIDevice(dxgiDevice.get(), insp.put());
    return insp.as<WGD3D11::IDirect3DDevice>();
}

// Save texture to BMP. Input texture expected format: BGRA (B8G8R8A8). We convert to RGB for 24-bit output.
static void save_staging_to_file(ID3D11Device* dev, ID3D11DeviceContext* ctx, ID3D11Texture2D* src,
                                 const std::filesystem::path& outPath)
{
    D3D11_TEXTURE2D_DESC desc{};

    src->GetDesc(&desc);

    D3D11_TEXTURE2D_DESC s = desc;
    s.Usage = D3D11_USAGE_STAGING;
    s.BindFlags = 0;
    s.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    s.MipLevels = 1;
    s.ArraySize = 1;
    s.MiscFlags = 0;
    ComPtr<ID3D11Texture2D> staging;

    if (FAILED(dev->CreateTexture2D(&s, nullptr, &staging)))
    {
        return;
    }

    ctx->CopyResource(staging.Get(), src);

    D3D11_MAPPED_SUBRESOURCE map{};

    if (FAILED(ctx->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &map)))
    {
        return;
    }

    std::vector<unsigned char> bgra(desc.Width * desc.Height * 4);

    for (UINT y = 0; y < desc.Height; ++y)
    {
        const unsigned char* rowSrc = (unsigned char*)map.pData + y * map.RowPitch;
        memcpy(&bgra[y * desc.Width * 4], rowSrc, desc.Width * 4);
    }

    ctx->Unmap(staging.Get(), 0);

    auto tmp = outPath;
    tmp += L".pending";
    static bool loggedProbe = false;

    if (!loggedProbe)
    {
        // Central pixel and 10x10 average diagnostics
        UINT cx = desc.Width / 2, cy = desc.Height / 2;
        if (cx < desc.Width && cy < desc.Height)
        {
            const unsigned char* pix = &bgra[(cy * desc.Width + cx) * 4];
            logf("probe_center bg=%u g=%u r=%u a=%u", pix[0], pix[1], pix[2], pix[3]);
            unsigned int sumB = 0, sumG = 0, sumR = 0, count = 0;

            for (int dy = -5; dy < 5; ++dy)
            {
                for (int dx = -5; dx < 5; ++dx)
                {
                    int x = (int)cx + dx;
                    int y = (int)cy + dy;

                    if (x >= 0 && y >= 0 && x < (int)desc.Width && y < (int)desc.Height)
                    {
                        const unsigned char* p2 = &bgra[(y * desc.Width + x) * 4];

                        sumB += p2[0];
                        sumG += p2[1];
                        sumR += p2[2];

                        ++count;
                    }
                }
            }

            if (count)
            {
                logf("probe_avg10x10 b=%u g=%u r=%u", sumB / count, sumG / count, sumR / count);
            }
        }

        loggedProbe = true;
    }

    if (BmpWriter::write(tmp, bgra.data(), (int)desc.Width, (int)desc.Height))
    {
        std::error_code ec;
        std::filesystem::rename(tmp, outPath, ec);

        if (ec)
        {
            std::filesystem::remove(outPath, ec);
            std::filesystem::rename(tmp, outPath, ec);
        }

        log_line("frame_written");
    }
}

int main()
{
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    log_line("capture_service_start");

    try
    {
        log_path("cwd", std::filesystem::current_path());
    }
    catch (...)
    {
    }

    log_path("frames_dir", frames_dir());

    int scanCount = 0;

    while (true)
    {
        DWORD pid = 0;
        if (!find_process(pid))
        {
            if ((scanCount++ % 15) == 0)
                logf("waiting_for_process names=[%S|%S]", kPrimaryProcessName, kAltProcessName);

            // fallback window title heuristic if process not yet enumerated (edge cases)

            HWND byTitle = find_window_by_title_substring(L"heroes of the storm");

            if (byTitle)
            {
                DWORD wpid = 0;

                GetWindowThreadProcessId(byTitle, &wpid);

                if (wpid)
                {
                    pid = wpid;
                    log_line("process_found_via_title");
                }
            }

            if (!pid)
            {
                std::this_thread::sleep_for(std::chrono::seconds(2));
                continue;
            }
        }
        else
        {
            log_line("process_found");
        }

        HWND hwnd = find_main_hwnd(pid);

        if (!hwnd)
        {
            // Try fallback by title if main window enumeration not ready yet
            hwnd = find_window_by_title_substring(L"heroes of the storm");
            if (hwnd)
                log_line("window_found_via_title");
        }

        if (!hwnd)
        {
            log_line("no_window_yet");
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        // Create D3D11 device
        ComPtr<ID3D11Device> d3d;
        ComPtr<ID3D11DeviceContext> ctx;
        D3D_FEATURE_LEVEL fl;

        if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                     nullptr, 0, D3D11_SDK_VERSION, &d3d, &fl, &ctx)))
        {
            log_line("device_fail");
            std::this_thread::sleep_for(std::chrono::seconds(2));
            continue;
        }

        auto interopDev = to_direct3d_device(d3d.Get());
        // Create GraphicsCaptureItem
        auto interop = winrt::get_activation_factory<WGC::GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        WGC::GraphicsCaptureItem item{nullptr};

        if (FAILED(interop->CreateForWindow(hwnd, winrt::guid_of<WGC::GraphicsCaptureItem>(), winrt::put_abi(item))))
        {
            log_line("create_item_fail");
            std::this_thread::sleep_for(std::chrono::seconds(2));
            continue;
        }

        auto size = item.Size();

        if (size.Width <= 0 || size.Height <= 0)
        {
            log_line("invalid_size");
            std::this_thread::sleep_for(std::chrono::seconds(2));
            continue;
        }

        logf("starting_capture width=%d height=%d", size.Width, size.Height);

        auto framePool = WGC::Direct3D11CaptureFramePool::CreateFreeThreaded(
            interopDev, WGD::DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);

        auto session = framePool.CreateCaptureSession(item);

        session.StartCapture();

        log_line("session_started");

        auto baseDir = frames_dir();

        struct SharedFrame
        {
            std::mutex m;
            ComPtr<ID3D11Texture2D> tex;
            UINT w = 0;
            UINT h = 0;
        } shared;

        std::atomic<bool> running{true};
        std::atomic<uint64_t> frameEvents{0};
        auto sessionStart = std::chrono::steady_clock::now();

        // Frame event: just copy latest frame into shared texture (GPU copy)
        auto token = framePool.FrameArrived(
            [&](WGC::Direct3D11CaptureFramePool const& sender, auto const&)
            {
                if (!running.load())
                    return;
                auto frame = sender.TryGetNextFrame();
                if (!frame)
                    return;
                frameEvents.fetch_add(1);
                logf("frame_event count=%llu", (unsigned long long)frameEvents.load());
                auto surface = frame.Surface();
                winrt::com_ptr<IDirect3DDxgiInterfaceAccess> access;
                if (FAILED(surface.as<IInspectable>()->QueryInterface(__uuidof(IDirect3DDxgiInterfaceAccess),
                                                                      access.put_void())))
                    return;
                ComPtr<ID3D11Texture2D> src;
                if (FAILED(
                        access->GetInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void**>(src.GetAddressOf()))))
                    return;

                D3D11_TEXTURE2D_DESC desc{};

                src->GetDesc(&desc);

                // Ensure a reusable texture of same size/format exists
                {
                    std::lock_guard<std::mutex> lock(shared.m);
                    if (!shared.tex || shared.w != desc.Width || shared.h != desc.Height)
                    {
                        desc.Usage = D3D11_USAGE_DEFAULT;
                        desc.BindFlags = 0;
                        desc.CPUAccessFlags = 0;
                        desc.MipLevels = 1;
                        desc.ArraySize = 1;
                        desc.MiscFlags = 0;

                        ComPtr<ID3D11Texture2D> newTex;

                        if (SUCCEEDED(d3d->CreateTexture2D(&desc, nullptr, &newTex)))
                        {
                            shared.tex = newTex;
                            shared.w = desc.Width;
                            shared.h = desc.Height;
                            logf("shared_texture_recreated w=%u h=%u", shared.w, shared.h);
                        }
                        else
                        {
                            return;
                        }
                    }
                    ctx->CopyResource(shared.tex.Get(), src.Get());
                }
            });

        // Saver thread: every 1s save the most recent shared texture (if any)
        std::atomic<bool> saverRun{true};

        std::thread saver(
            [&]
            {
                int saveIdx = 0;
                auto next = std::chrono::steady_clock::now();
                while (saverRun.load())
                {
                    next += std::chrono::seconds(1);
                    std::this_thread::sleep_until(next);
                    if (!running.load())
                        break;
                    // Stall detection (no frame events yet after 2s)
                    if (frameEvents.load() == 0 && std::chrono::duration_cast<std::chrono::milliseconds>(
                                                       std::chrono::steady_clock::now() - sessionStart)
                                                           .count() > 2000)
                    {
                        log_line("capture_stalled_no_events");
                    }
                    ComPtr<ID3D11Texture2D> texCopy;
                    UINT w = 0, h = 0;
                    {
                        std::lock_guard<std::mutex> lock(shared.m);
                        if (!shared.tex)
                        {
                            continue;
                        }
                        texCopy = shared.tex;
                        w = shared.w;
                        h = shared.h;
                    }
                    auto now = std::chrono::system_clock::now();
                    auto msEpoch = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch());
                    auto secEpoch = std::chrono::duration_cast<std::chrono::seconds>(msEpoch);
                    auto msPart = msEpoch - secEpoch;
                    std::time_t tt = std::chrono::system_clock::to_time_t(now);
                    std::tm utc{};
                    gmtime_s(&utc, &tt);
                    wchar_t name[128];
                    swprintf(name, 128, L"%04d-%02d-%02dT%02d-%02d-%02d.%03lldZ_%05d.bmp", utc.tm_year + 1900,
                             utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec,
                             static_cast<long long>(msPart.count()), saveIdx++);
                    save_staging_to_file(d3d.Get(), ctx.Get(), texCopy.Get(), baseDir / name);
                    logf("frame_saved index=%d scheduler w=%u h=%u events=%llu", saveIdx - 1, w, h,
                         (unsigned long long)frameEvents.load());
                }
            });
        // Monitor process
        HANDLE hProc = OpenProcess(SYNCHRONIZE, FALSE, pid);
        if (!hProc)
        {
            log_line("open_proc_fail");
            framePool.FrameArrived(token);
            framePool.Close();
            session.Close();
            continue;
        }
        DWORD exitCode = 0;
        bool signaled = false;
        auto start = std::chrono::steady_clock::now();
        while (true)
        {
            DWORD w = WaitForSingleObject(hProc, 500);
            if (w == WAIT_TIMEOUT)
            {
                continue;
            }
            signaled = true;
            GetExitCodeProcess(hProc, &exitCode);
            // Give a brief grace period (up to 750ms) to flush a last frame
            auto graceStart = std::chrono::steady_clock::now();
            while (std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - graceStart)
                       .count() < 750)
            {
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            break;
        }
        CloseHandle(hProc);
        running = false;
        framePool.FrameArrived(token);  // revoke
        session.Close();
        framePool.Close();
        saverRun = false;
        if (saver.joinable())
            saver.join();
        if (signaled)
            logf("process_ended exit_code=%lu uptime_ms=%llu", (unsigned long)exitCode,
                 (unsigned long long)std::chrono::duration_cast<std::chrono::milliseconds>(
                     std::chrono::steady_clock::now() - start)
                     .count());
        else
            log_line("process_ended");
    }
    return 0;
}
